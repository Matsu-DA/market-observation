# market-observation

長期時系列の市場データを GitHub リポジトリ単体で安定蓄積するための
**観測専用** インフラ。分析・予測・アラート・UI は持たない。

---

## 設計思想

このリポジトリの本体は「市場データ」ではなく **「認知履歴」** である。

> **Git history = epistemic history**
>
> 通常のデータベースは「最新状態」を保存する。
> このリポジトリは「当時の認識」を保存する。
>
> 観測時点での世界認識を保存する。
> 後から修正された "真実" ではなく、その時点で自分に見えていた "真実" を残す。

immutable / append-only / rewrite 禁止 / revision tracking 非対応 は
すべてこの一貫した思想から導かれる。

---

## する / しない

| する | しない |
|---|---|
| 日次の immutable な観測値保存 | 解析・予測・signal 生成 |
| DuckDB から読める Parquet レイク | ダッシュボード・WebUI |
| Hive 完全準拠の partition 構造 | アラート通知 |
| 取得失敗のサマリログ | 取引ロジック |
| schema 固定 (PyArrow) | revision tracking |
| 1 観測日 = 1 immutable ファイル | upsert / merge / rewrite |
| | intraday データ保存 |

---

## データソース

| Source | Dataset |
|---|---|
| FRED | DGS2, DGS10, DGS30, DFII10, T10YIE, T10Y2Y, BAMLH0A0HYM2, BAMLC0A0CM, VIXCLS, SOFR, DFF, RRPONTSYD, DTWEXBGS |
| Yahoo Finance | SPY, QQQ, RSP, IWM, HYG, JNK, TLT, SOXX, XLF, KRE, GLD, USO, CPER, DBC, XLE |

---

## 観測カテゴリ

dataset 一覧は単なる設定ではなく観測哲学そのもの。
「なぜそれを見るのか」を未来の自分が思い出せるように残す。

| カテゴリ | dataset | 何を見るか |
|---|---|---|
| 金利 | DGS2/10/30, DFII10, T10YIE, T10Y2Y, TLT | term structure / 実質金利 / inflation expectation / duration trade |
| 信用 | BAMLH0A0HYM2, BAMLC0A0CM, HYG, JNK | HY/IG OAS / ETF liquidity stress / dislocation |
| 流動性 | SOFR, DFF, RRPONTSYD | 担保調達金利 / Fed Funds 乖離 / RRP 枯渇 |
| ドル | DTWEXBGS | 世界流動性 / EM 圧迫 / dollar shortage |
| 株式 | SPY, QQQ, RSP, IWM | 大型 / テック / 均等 / 小型 |
| AI/半導体 | SOXX | AI CAPEX 上流 (HBM / DC / 電力) |
| 金融セクター | XLF, KRE | 銀行ストレス / CRE / deposit flight (先行指標) |
| ボラティリティ | VIXCLS | 株式恐怖指数 (債券版 MOVE は安定 source 確保後に追加予定) |
| コモディティ | GLD, USO, CPER, DBC | 金 / 原油 / 銅 / 広範コモディティ |
| エネルギー株 | XLE | エネルギー企業利益面 (USO の先物ノイズを補完) |

---

## ストレージ構造

```
data/
  source=<fred|yahoo>/
    dataset=<NAME>/
      year=YYYY/month=MM/day=DD/data.parquet
logs/
  summary_<ISO8601>.json
  summary_<ISO8601>_backfill.json
```

すべての partition key (`source`, `dataset`, `year`, `month`, `day`) を
ディレクトリ階層として保持。DuckDB / Polars / Spark / Athena から
`hive_partitioning = true` でそのまま読める。

### スキーマ

共通列: `observed_date (date32)`, `source`, `dataset`, `ingested_at (timestamp[us, UTC])`

- **FRED**: + `value (float64)`
- **Yahoo**: + `open / high / low / close / adj_close (float64)`, `volume (int64, nullable)`

### Invariant

`(source, dataset, observed_date)` は常に **1 行ちょうど**。
違反時は `DataIntegrityError` で書き込みを中止し、`logs/summary_*.json` に記録する。

---

## セットアップ

1. リポジトリ secret に `FRED_API_KEY` を設定
   - <https://fredaccount.stlouisfed.org/apikeys> で取得
2. Actions タブで `daily-ingest` と `weekly-backfill` を一度手動実行（`workflow_dispatch`）

cron スケジュール:
- `daily-ingest`: 毎日 UTC 22:00 (JST 07:00, 米市場クローズ後)
- `weekly-backfill`: 毎週日曜 UTC 23:00

---

## ローカル実行

```bash
pip install -r requirements.txt
export FRED_API_KEY=...
python -m scripts.daily_ingest
python -m scripts.weekly_backfill
```

---

## DuckDB で読む

```bash
duckdb
```

```sql
-- 直近 1 年の 10Y 金利
SELECT observed_date, value
FROM read_parquet('data/source=fred/dataset=DGS10/**/*.parquet',
                  hive_partitioning = true)
WHERE observed_date >= CURRENT_DATE - INTERVAL 1 YEAR
ORDER BY observed_date;
```

`sql/examples.sql` にサンプルクエリを置いてある。

---

## 制約と注意事項

### 仕様として明示する制約

- **revision tracking を行わない**
  `observed_date` あたり 1 行 immutable。FRED の事後改訂値は反映されない。
  これは設計思想に基づく意図的な選択であり、バグではない。

- **`observed_date` は「市場データ提供元の日付」**
  UTC calendar truth ではない。NASDAQ の `2026-05-13` は UTC では翌日に跨ぐ場合がある。
  FRED と Yahoo の同一 `observed_date` で SQL JOIN する際は、この前提を踏まえて結果を解釈すること。

- **`ingested_at` の固定定義**
  `storage.write_immutable()` 実行直前の UTC timestamp に固定。
  fetch 開始時刻でも provider response 時刻でも commit 時刻でもない。
  将来 forensic（「いつ書かれた値か」）を再現可能にするための一意定義。

- **取得対象日は Provider 側ロジックに依存**
  cron 実行時点では前営業日のデータしか返らないケースがある。
  `weekly_backfill` が翌週に拾うため最終的には埋まる。

- **OHLCV のうち時刻はバーの日付に限定**。intraday は保存しない。

- **Provider が canonical schema を保証する**
  yfinance の tz 揺れなどは Provider 層で吸収する設計。

### 既知の挙動

- **Provider behavior itself is not immutable**
  Provider (FRED API / yfinance) の挙動自体は時とともに変わる：split adjustment 仕様変更、
  auto_adjust デフォルト変更、tz 仕様変更、holiday handling 変更 など。
  本リポジトリは revision tracking を行わないため、
  「同じ `observed_date` が将来別の値で取得される」可能性は思想上許容する。

- **Concurrent workflows may fail harmlessly due to immutable race**
  cron + workflow_dispatch + manual rerun が同時実行された場合、
  片方の `git push` が non-fast-forward で失敗する可能性がある。
  immutable ファイルしか追加しないため、失敗側は次回実行で自然に埋まる。
  lock は意図的に持たない（運用都合より思想を優先）。

### 長期運用

- **このリポジトリは archive system ではない**
  active observation window を主目的とする。
  `1 day = 1 parquet` 設計は Git object 数を増やすため、数年スケールで
  clone 速度・gc・packfile サイズ・Actions checkout 速度が劣化する。

  超長期保存が必要になった時点で以下のいずれかへ退避する:
  - 年単位の archive リポジトリへ移動
  - object storage (S3 / R2 / GCS) へエクスポート
  - GitHub Release artifact として固定

  active window は直近 1〜3 年程度を想定。
  当面の運用緩和策:
  - `git clone --depth 1`
  - `git clone --filter=blob:none`

### 既知の設計負債

- 現在は `1 dataset = 1 HTTP call`。yfinance は複数ティッカーまとめて取得可能。
  dataset 数が大幅に増えた場合は batch fetch 最適化を検討。
  ただし、現在の "fetch 前 exists 全件チェック" 最適化は
  `1 dataset = 1 HTTP request` という暗黙前提に依存しているため、
  batch fetch 導入時は storage-first 最適化を再設計する必要がある。

---

## 採用判断の防波堤

将来「便利だから」を理由に以下を入れたくなったら、本セクションに戻ること:

- rewrite / upsert / merge → 不採用（immutable を壊す）
- revision tracking → 不採用（"当時の認識" を上書きする）
- 集計・派生テーブル → 不採用（観測層と分析層を混ぜる）
- semantic / metadata / manifest レイヤー → 不採用（個人運用にはオーバーキル）

「小さいのに壊れにくい」が最重要原則。
