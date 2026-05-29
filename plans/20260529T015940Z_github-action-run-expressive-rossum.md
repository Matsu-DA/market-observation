# daily-ingest 失敗の原因特定と「エラー可視化」改善

## Context（なぜこの変更をするか）

`daily-ingest` ワークフローが exit code 1 で失敗。最新を pull して実データを確認した結果、
**根本原因は FRED API (`api.stlouisfed.org`) のサーバー側障害（HTTP 504 Gateway Timeout）**と確定した。

### 確定した根本原因（証拠）
- 失敗コミット `00152cc 2026-05-28 ingest: ... (15 written, 13 errors)`。
  サマリー `logs/summary_20260528T231558Z.json` は **13 FRED 全滅・全て `"ValueError: None"`**、
  Yahoo 15 件は成功。前日 `2026-05-27` は 0 errors で正常。
- error_ratio = 13/28 = 0.46 > `ERROR_RATIO_THRESHOLD`(0.30) → `daily_ingest.main()` が `return 1` → ジョブ失敗。
  （※ クラッシュではない。サマリーもコミットも正常に残っている。）
- 直接プローブ（`curl`）で確認：`api.stlouisfed.org` は key 無/ダミー key/XML/JSON いずれも
  **HTTP 504（HTML エラーページ）またはタイムアウト**を返す。一方 `pypi.org`・`api.github.com`・
  FRED の Web サイト `fred.stlouisfed.org` は 200。→ 障害は **FRED の API ホスト固有**で、
  504 は認証前のゲートウェイで発生するため **API キーの有無・正否は無関係**。
- `fredapi` は 504 の HTML 本文から error message を取り出せず `ValueError(None)` を送出
  → `ingest.py:92` の `f"{type(exc).__name__}: {exc}"` が `"ValueError: None"` になる（＝無情報）。
- 依存ドリフトでもない（Yahoo と parquet 書き込みは正常）。コードのバグでもない。
- プローブ時点（2026-05-29）でも FRED API は degraded のまま。

### 結論と方針（ユーザー合意）
- これは**外部障害**。FRED API 復旧後に再実行（または翌日の定期実行）すれば緑に戻る。コードでは直せない。
- 合意した対応範囲は **「エラー可視化だけ」**：振る舞い（ソース障害で失敗＝正しいアラート）は維持し、
  `"ValueError: None"` / `"exited non-zero"` ではなく **「FRED HTTP 504 / API 到達不可」と
  CI ログ・サマリーに明示**する。リトライ追加・ソース別しきい値などの耐性強化は今回**見送り**。

---

## 実装計画

### Change 1: FRED エラーを情報のあるものにする — `scripts/providers/fred.py`
ハッピーパス（`self._fred.get_series`）は一切変更しない。**失敗時のメッセージだけ**改善する。

- 専用例外 `class FredApiError(RuntimeError)` を追加。
- `__init__` で `self._api_key` を保持（診断プローブで再利用）。
- `fetch()`（または `_get_series` 呼び出し箇所）を try/except で包む：
  - `fredapi` が **空/None メッセージの `ValueError`**（＝HTTP エラー本文を解釈できなかった 504 等の兆候）を投げた場合、
    `self._diagnose()` を呼び、`FredApiError(f"FRED API unreachable ({diag}); dataset={dataset}")` を送出。
  - `fredapi` が **説明的メッセージ**を返した場合（例：存在しない series）は情報があるので、
    dataset 文脈だけ付けて `FredApiError(f"FRED API error for {dataset}: {exc}")` を送出。
- `_diagnose()`：**1回だけ**（インスタンスにメモ化）、短いタイムアウト（≈10s）で `requests.get` を
  FRED API の軽量エンドポイントに投げ、実際の HTTP ステータスを取得して文字列化：
  - 例：`"HTTP 504 Gateway Time-out"` / `"HTTP 403"` (key 失効の判別) / `"connection timed out"`。
  - 同一 run 内の 13 データセット失敗で 13 回プローブしない（メモ化）。15分ジョブのタイムアウトも回避。
- 効果：`ingest.py` がそのまま拾い、サマリーに
  `FredApiError: FRED API unreachable (HTTP 504 Gateway Time-out); dataset=DGS10` と記録される。
- `tenacity` リトライ設定は**変更しない**（耐性強化は見送り合意）。

### Change 2: CI に実エラーを浮上させる — `.github/workflows/daily-ingest.yml`
「失敗しても部分成果を必ずコミット → その後ジョブを赤にする」現行意図は維持しつつ、実エラーを可視化：

```yaml
      - name: Run daily ingest
        id: ingest
        continue-on-error: true          # 犯人ステップ自体を赤くしつつ後続(commit)へ進む
        env:
          FRED_API_KEY: ${{ secrets.FRED_API_KEY }}
        run: |
          set -o pipefail
          python -m scripts.daily_ingest 2>&1 | tee ingest_run.log

      - name: Commit & push
        if: always()                     # ingest 失敗時も部分成果をコミット（現行ロジックは不変）
        run: |
          ...

      - name: Surface ingest failure
        if: steps.ingest.outcome == 'failure'
        run: |
          echo "::error::daily ingest failed — 下記トレース参照（多くはデータソース障害、例: FRED HTTP 5xx）"
          echo "----- tail of ingest output -----"
          tail -n 50 ingest_run.log || true
          exit 1
```

- `continue-on-error: true` で実際の "Run daily ingest" ステップが赤くなる（不透明な下流ステップ任せをやめる）。
- `tee` で出力を保存し、失敗報告ステップで `tail` して実ログ（Change 1 で情報化済み）を直接表示。
- `::error::` アノテーションで実行サマリー上部にエラーを浮上。
- 旧 "Fail on high error ratio" を "Surface ingest failure"（`outcome == 'failure'` 判定）に置換。
- `Commit & push` に `if: always()` を付与。

### Change 3: 同型修正 — `.github/workflows/weekly-backfill.yml`
weekly-backfill も daily と**同一の不透明パターン**を持つため、Change 2 と同じ3点修正を適用（整合性のため）。
FRED プロバイダ（Change 1）は共通の `run_ingest` 経由で weekly にも自動的に効く。

---

## 変更対象ファイル
- `scripts/providers/fred.py`（Change 1：FredApiError + 診断プローブ）
- `.github/workflows/daily-ingest.yml`（Change 2：CI 可視化）
- `.github/workflows/weekly-backfill.yml`（Change 3：同型 CI 可視化）

## やらないこと（スコープ外・合意済み）
- リトライ拡張・5xx リトライ・ソース別しきい値などの耐性強化。
- 依存の全面ピン留め／ロックファイル化。
- クラッシュ時サマリー記録などの追加観測。

## 検証方法
- **ローカル単体確認**：`FredProvider` を生成し、`self._fred.get_series` を `ValueError(None)` を投げるよう
  monkeypatch → `fetch()` が `FredApiError` を投げ、メッセージに HTTP ステータス/到達不可が含まれることを確認。
  説明的 `ValueError("Bad Request...")` のケースはメッセージがそのまま保持されることも確認。
- **診断プローブの実地確認**：現在 FRED API は 504 のため、`_diagnose()` が実際に
  "HTTP 504" 相当を返すことをローカルで確認できる。
- **ワークフロー文法**：変更後 YAML を目視（`actionlint` があれば実行）。
- **CI 実地確認（ユーザー承認のうえ `workflow_dispatch`）**：
  - FRED 障害中：実行すると "Run daily ingest" が赤、"Surface ingest failure" に
    `FredApiError: FRED API unreachable (HTTP 504 ...)` の tail が出る、`::error::` が上部に出る。
  - FRED 復旧後：従来どおり緑でコミット&プッシュされる。
- **即時対応の補足**：FRED API 復旧前の再実行はやはり失敗するが、メッセージが
  `"ValueError: None"` → `"FRED HTTP 504 / 到達不可"` に変わり原因が即判別できる。復旧後の再実行で緑化。
