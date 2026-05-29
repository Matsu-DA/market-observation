# CI 自動コミットの parquet add/add 衝突を解消する

## Context（なぜ直すか）

`daily-ingest` / `weekly-backfill` の自動コミットステップが `git pull --rebase` 時に
`.parquet` バイナリの **add/add 衝突**で落ち、CI が exit 1 で失敗する。

### 根本原因
1. **並行/遅延実行の競合**: 両ワークフローに `concurrency:` ガードが無い。schedule・
   workflow_dispatch・遅延した前日ランなどが重なると、ある run の checkout 後〜push 前に
   別 run が origin/main を進める。今回 runner は `33666de`(05-27分) を checkout したが、
   実行中に origin が `00152cc`(05-28分, day=28 を追加) → `0f40d43` まで進んだ。
2. **stale checkout により skip-if-exists が効かない**: ローカルには day=28 が無いため
   `exists()` が False を返し、ingest が day=28 を再取得・再書き込み。origin 側も同じ
   day=28 を「新規追加」済み → 同一パスの add/add 衝突。
3. **バイナリは 3-way マージ不可**: `.gitattributes` の `*.parquet binary`(=`-merge`) により
   Git は中身をマージせず CONFLICT で停止。自動解決の指定が無いため rebase が中断する。

### 方針決定（ユーザー確認済み）
同一取引日の中身が食い違った場合は **再取得した新しい版を優先**する
（provider の adjusted close / volume 改訂を反映）。rebase 中は `theirs`（=ローカルの
再取得コミット）を採用する = **`-X theirs`**。
※ ingest 本体の skip-if-exists（不変書き込み）は変更しない。衝突解決はレース時のみ作用する。

## 変更内容

### 1. 競合の根本抑止: 共有 concurrency グループ
両ワークフローに同名グループを付け、daily/weekly/dispatch を直列化する。
`permissions:` ブロックの直後（`jobs:` の前）に追加：

```yaml
concurrency:
  group: market-data-push
  cancel-in-progress: false
```

- `cancel-in-progress: false`: 実行中ランは止めず、後続は pending で待機（取りこぼし防止）。

### 2. push を「リトライ + 決定的衝突解決」ループに置換
`.parquet` の add/add を `-X theirs` で自動解決し、push 拒否（リモート前進）時は再同期して
リトライ。解決不能や全試行失敗は**明示的に exit 1**して原因をログに出す
（直近コミット 0f40d43 の「実原因を可視化」方針を踏襲）。

#### `daily-ingest.yml`（54–55 行目を置換）
現状:
```bash
          git pull --rebase origin "${GITHUB_REF_NAME:-main}"
          git push origin "HEAD:${GITHUB_REF_NAME:-main}"
```
置換後:
```bash
          branch="${GITHUB_REF_NAME:-main}"
          for attempt in 1 2 3 4 5; do
            git fetch origin "$branch"
            if ! git rebase -X theirs "origin/$branch"; then
              echo "::warning::rebase に未解決の衝突 (attempt $attempt) — abort して再試行"
              git rebase --abort
              sleep $((attempt * 3))
              continue
            fi
            if git push origin "HEAD:$branch"; then
              echo "pushed on attempt $attempt"
              exit 0
            fi
            echo "::warning::push 拒否 — リモートが前進 (attempt $attempt) — 再同期"
            sleep $((attempt * 3))
          done
          echo "::error::5 回試行しても snapshot を push できませんでした"
          exit 1
```

#### `weekly-backfill.yml`（53–54 行目を置換）
同一の置換後ブロックを適用（コミットメッセージ生成部 49–52 行はそのまま）。

## 影響を受けるファイル
- `.github/workflows/daily-ingest.yml` — concurrency 追加 + push ループ置換
- `.github/workflows/weekly-backfill.yml` — concurrency 追加 + push ループ置換
- ingest 本体 (`scripts/ingest.py` 等) は**変更しない**（skip-if-exists は維持）

## なぜこの解決法か
- `concurrency` で**競合そのものを大幅に減らし**、`-X theirs` リトライで**残存レースを
  決定的に吸収**する二段構え。
- `.gitattributes merge=ours` カスタムドライバ案より自己完結（CI 側設定不要）で、かつ
  リモート前進時の push リトライも同時に担保できる。
- rebase 中の `theirs` = 後から取得したローカル版 = ユーザー選択の「再取得版を優先」と一致。

## 検証（end-to-end）
1. **ローカルで add/add 衝突を再現し解決を確認**（一時クローンで安全に）:
   ```bash
   tmp=$(mktemp -d); git clone . "$tmp/up"; git -C "$tmp/up" config receive.denyCurrentBranch ignore
   git clone "$tmp/up" "$tmp/a"; git clone "$tmp/up" "$tmp/b"
   # 同一パスに異なる中身の .parquet を a/b 双方で add → commit
   # a を push、b で「上記 push ループ」を実行し、b の版が採用され push 成功することを確認
   ```
   期待: rebase が `-X theirs` で自動解決し、b のバイト列が最終結果になる。
2. **構文チェック**: `actionlint .github/workflows/*.yml`（無ければ YAML パーサで lint）。
3. **本番系**: マージ後に `daily-ingest` を workflow_dispatch で手動実行し、
   Actions ログに `pushed on attempt 1`（または再試行後の成功）が出ること、
   今回のような CONFLICT 中断が再発しないことを確認。
4. 現在の失敗ランは復旧不要（runner は揮発、ローカル working tree は clean）。修正反映後の
   次回 schedule または手動 dispatch から新ロジックが効く。
