# market-observation 改善実装計画（レビュー反映版）

## Context

プロジェクト全体レビューで見つかった改善点のうち、ユーザーが選択した 3 項目を実装する:

1. **FRED HTTP 5xx がリトライされない**: `fredapi` は HTTPError を `ValueError` に変換するため、`fred.py` の tenacity リトライ（`ConnectionError, TimeoutError, OSError` 対象）をすり抜ける。一時的な 504 で FRED 13 datasets が即エラー計上され error ratio 46% > 30% で run が赤くなる。
2. **サイレントなデータ途絶を検知できない**: Yahoo ティッカー廃止等では例外でなく空 DataFrame が返り `skipped_empty` に静かに積まれるだけ。永続的なデータ途絶に気づく仕組みがない。
3. **テスト不在**: リポジトリの価値である invariant（1 partition = 1 行、atomic write、exists 健全性判定、provider 正規化）を守るテストがゼロ。

設計思想（immutable / KISS / 最小依存）は維持する。依存バージョン固定は今回スコープ外（ユーザー判断）。

**レビューでの指摘反映（2 巡）**: (a) FRED transient 判定を文字列シグネチャ依存から exception chaining ベースに変更（`__context__` は「あれば使う」、シグネチャ判定は互換性フォールバック）、(b) ParseError 単独は transient にしない（HTTPError 5xx/429 の裏付けがある場合のみリトライ）、(c) `DEFAULT_STALE_AFTER_DAYS` 命名 + README に全 dataset 共通値である旨を明記、(d) atomic write 失敗テスト追加（失敗注入は `pq.write_table` の 1 点のみ）、(e) ConnectionError の既存リトライ回帰テスト追加、(f) tests.yml に `pull_request` トリガー追加、(g) HealthIssue 抽象化は将来課題として明記。CI のテストコマンドは `pytest -q`（誤記ではない）。

## 変更内容

### 1. FRED transient エラーのリトライ — `scripts/providers/fred.py`

**判定根拠（fredapi の `__fetch_data`。GitHub master ブランチを 2026-07-08 に確認。`requirements.txt` は `fredapi>=0.5` 非固定のため、実装時に pip でインストールされた実バージョンのソースで同挙動を再確認する — Step A の作業に含める）**:
```python
except HTTPError as exc:
    root = ET.fromstring(exc.read())
    raise ValueError(root.get('message'))
```
- `raise ... from` なしの except ブロック内 raise のため、元の `urllib.error.HTTPError`（`.code` 付き）が **`ValueError.__context__` に必ず残る**（Python の例外チェーン仕様であり fredapi のメッセージ書式に依存しない）。
- エラー本文が XML でない場合（5xx の HTML ページ等）は `ET.fromstring(exc.read())` から `xml.etree.ElementTree.ParseError` が漏れる。この場合も `__context__` に HTTPError が残る。

**実装**:
- `FredTransientError(FredApiError)` を新設（リトライ対象マーカー）。
- `_get_series` 内で `self._fred.get_series()` を try し、`except (ValueError, ET.ParseError) as exc:` で捕捉。判定は以下の優先順:
  1. **例外チェーンに `urllib.error.HTTPError` があれば**（`__context__`/`__cause__` を辿る）その `code` で判定: `code >= 500` または `code == 429` → `FredTransientError`（リトライ対象）/ それ以外（4xx）→ そのまま re-raise（リトライしない）
  2. **チェーンに HTTPError が無い場合のフォールバック**: `ValueError` で message が空/"None"（既知の 504 シグネチャ）のときのみ transient 扱い。これは fredapi が将来 `raise ... from None` 等に変えて chain が切れた場合の**互換性維持の最後の保険**であることをコメントに明記する。
  3. **`ParseError` 単独（HTTPError がチェーンに無い）→ transient にしない**でそのまま re-raise。ParseError は 5xx の HTML だけでなく API 仕様変更や恒久的な XML 不整合でも起きるため、HTTP ステータスの裏付けがある場合だけリトライする。
  4. message 付きの純粋な `ValueError`（例: "Bad Request. The series does not exist."）→ そのまま re-raise。
- **例外チェーン探索は小ヘルパー `_find_http_error(exc)` に集約**: `__cause__` を優先し、無ければ `__suppress_context__` でない限り `__context__` を辿る（`raise ... from None` は `__context__` に元例外が残るが `__suppress_context__=True` になるため「chain 無し」として扱う）。ロジックが 1 か所になりテストも直接当てられる。
- **判定根拠のコードコメント**: fredapi が HTTP エラーを ValueError に潰す実装であること、`__context__` は「あれば利用する」位置付けであることを簡潔に書く。fredapi の実装コードの逐語引用はしない（fredapi 更新時にコメント・コード・引用の三重管理になるため、根拠の要約に留める）。
- tenacity の `retry_if_exception_type` に `FredTransientError` を追加。既存の `stop_after_attempt(RETRY_ATTEMPTS)` / `wait_exponential` はそのまま。
- `fetch()` の except 節を再構成:
  - `except FredTransientError` → リトライ枯渇後。現行同様 `_diagnose()` を添えて `FredApiError("FRED API unreachable (...)")` に変換。
  - `except (ValueError, ET.ParseError)` → 非 transient。現行どおり `FredApiError("FRED API error for ...")` に変換（detail が空なら diagnose 添付の現行分岐を維持）。

### 2. staleness 検知 — `scripts/config.py`, `scripts/ingest.py`, entry 2 本, README

- `config.py`: `DEFAULT_STALE_AFTER_DAYS = 7` を追加（暦日）。現在は全 dataset が日次系列なので単一デフォルトで足りるが、将来 weekly/monthly 系列を追加する際に dataset 毎の閾値へ拡張できる命名にしておく（今回は per-dataset 設定は実装しない）。
- `ingest.py`:
  - `IngestResult` に `stale_datasets: list[dict] = field(default_factory=list)` を追加、`as_dict()` にも含める。
  - `run_ingest()` の **ingest ループ全完了後**・`_write_summary` 前に検査: plan の各 `(provider, dataset)` について、既存ヘルパー `_target_days(reference, DEFAULT_STALE_AFTER_DAYS)` を再利用して得た日付リストで `storage.exists()` を確認し、1 件も無ければ `{"source": ..., "dataset": ...}` を追加し `log.warning("stale_dataset ...")`。
  - exists() の走査は 28 datasets × 8 日 ≈ 224 回のメタデータ読みで現状は問題なし。dataset 数が大きく増えたら `storage.list_partitions()` 的な API への置換を検討（コメントで言及するに留める）。
- `daily_ingest.py` / `weekly_backfill.py` の `main()`: `result.stale_datasets` が非空なら、`os.environ.get("GITHUB_ACTIONS") == "true"` のときだけ `print(f"::warning::stale datasets: ...")` を出力。exit code は変えない（warning 止まり。数週間続いたら人が見る運用）。
- `README.md`: サマリログ節に `stale_datasets` フィールドの一行説明を追加。あわせて「現在は全 dataset 共通の `DEFAULT_STALE_AFTER_DAYS`（7 暦日）を使用。weekly/monthly 系列を追加する際は dataset 毎の閾値へ拡張する」旨を一文追記。
- **将来課題（今回は実装しない・計画に記録のみ）**: 現在は「stale という結果」だけを持つが、健全性の種類が増えたら（schema mismatch / provider error / duplicate 等）`HealthIssue(dataset, reason, last_seen)` のような概念に一般化する余地がある。

### 3. テスト — `tests/` 新設, `requirements-dev.txt`, `.github/workflows/tests.yml`, README

- `requirements-dev.txt`: `-r requirements.txt` + `pytest>=8`。
- `tests/test_storage.py`（`monkeypatch.setattr(storage, "DATA_DIR", tmp_path)` で隔離）:
  - `write_immutable`: 1 行書込→True / 同一 partition 再書込→False（immutability）/ `len(df) != 1` → `DataIntegrityError` / enrich 列（observed_date, source, dataset, ingested_at）と schema 列順の検証。
  - **atomic write 失敗**: `pq.write_table` を monkeypatch で例外化し（`os.replace` 前で失敗させる 1 点のみ。実装依存の過剰モックは避ける）、(i) `data.parquet` が存在しない（中途半端なファイルが残らない）、(ii) `.tmp_*` ファイルが cleanup されている、の両方を検証。atomic write の最重要保証。
  - `exists`: 無し→False / 0 byte→False / 壊れた parquet→False / 健全 1 行→True / 2 行ファイル→False。
  - `partition_path`: zero-pad 形式。 `_coerce_date`: date / datetime / pd.Timestamp / str / np.datetime64。
- `tests/test_fred_provider.py`（`monkeypatch.setattr("tenacity.nap.time.sleep", lambda s: None)` で待ち時間排除、`requests.get` はモック）:
  - 正常 Series → canonical df（列・dtype）。
  - **fredapi の実挙動を再現したモック**（`except HTTPError: raise ValueError(...)` を実際に実行して `__context__` を作る）で: 504 → RETRY_ATTEMPTS 回リトライ後 `FredApiError`（unreachable 文言）/ 404 → リトライ 1 回のみ / message 付き ValueError（series 不存在）→ リトライなし / **ParseError 単独（HTTPError チェーン無し）→ リトライなし** / ParseError + HTTPError 5xx チェーン → リトライあり / **chain 無し ValueError("None") → フォールバックで transient 扱い**（テスト内で実際に `raise ValueError("None") from None` を実行して作る。`__context__` は残るが `__suppress_context__=True` になるため、`_find_http_error` が「chain 無し」と判定することの検証を兼ねる）。
  - **既存リトライの回帰確認**: `ConnectionError` を raise するモックで、`FredTransientError` 追加後も既存の `(ConnectionError, TimeoutError, OSError)` リトライが維持されていることを確認する。
  - このテスト自体が「fredapi が HTTP エラーを ValueError に潰す」前提の生きたドキュメントになる。
- `tests/test_yahoo_provider.py`（`_download` を monkeypatch）:
  - tz-naive index / tz-aware index → observed_date が NY セッション日付になる。
  - MultiIndex 列の flatten、volume 欠落時の Int64 NA、空 DataFrame → 空 canonical df。
- `tests/test_ingest.py`（`_build_providers` と `scripts.ingest.FRED_DATASETS` / `YAHOO_DATASETS` を monkeypatch、storage は tmp_path）:
  - written / skipped_exists / skipped_empty / errors の計上、summary JSON 書出し、stale_datasets 検知（データ有→非検知、閾値超欠落→検知）。
- `.github/workflows/tests.yml`: `on: push`（`paths: scripts/**, tests/**, requirements*.txt`）+ **`pull_request`（同 paths）** + `workflow_dispatch`。ubuntu-latest / Python 3.12 / `pip install -r requirements-dev.txt` / `pytest -q`。paths フィルタにより日次データコミットでは走らない。
- `README.md` ローカル実行節にテスト実行コマンドを追記。

## 実装ステップ（sonnet サブエージェントに委譲、グローバル CLAUDE.md 準拠）

1. Step A: fred.py リトライ修正（変更内容 1。判定根拠はコメントに要約を残す。実装引用はしない）
2. Step B: staleness 検知（変更内容 2）
3. Step C: テスト一式 + dev requirements + tests.yml + README 追記（変更内容 3。A/B の実装後に着手し、A/B の挙動もテストに含める）
4. メインループ: 各成果物レビュー → テスト実行 → 検証 → 報告

## 検証

- `pytest -q` 全緑（新規テストが A/B の挙動を直接検証する。特に atomic write 失敗と FRED 例外チェーン判定）。
- `python -c "from scripts.providers.fred import FredProvider"` 等で import 健全性確認。
- 実データへの書込・git commit は行わない。

## 完了時

- 本計画ファイルを `plans/` へ ISO8601 prefix 付きでアーカイブ（確立済み運用）。
- 「修正と検証すべて完了しました。」+ コンベンショナルコミットメッセージ案を提示。自動コミットはしない。
