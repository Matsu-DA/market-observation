# Market Observation Repository — 実装計画 (v8)

## Context

長期時系列の市場データ（FRED マクロ指標 + Yahoo Finance ETF）を
GitHub リポジトリ単体で安定蓄積するためのインフラを構築する。

- **目的**: 「観測専用」。分析は別レイヤーで後から行う
- **方針**: Parquet を DB 扱いせず、**append-only event log** として扱う
- **運用**: 個人運用・日次低頻度・**1 日 1 ファイル immutable**
- **解析**: ユーザーが DuckDB / Polars / Spark で後から読む

---

## 設計思想（最重要）

このリポジトリの本体は「市場データ」ではなく **「認知履歴」** である。

> **Git history = epistemic history**
>
> 通常のデータベースは「最新状態」を保存する。
> このリポジトリは「当時の認識」を保存する。
>
> 観測時点での世界認識を保存する。
> 後から修正された "真実" ではなく、その時点で自分に見えていた "真実" を残す。

未来の自分が、過去の自分が何を見ていたかを検証できる構造として設計する。
immutable / append-only / rewrite 禁止 / revision tracking 非対応 はすべて
この一貫した思想から導かれる前提であり、技術選定ではない。
この前提を崩す変更は採用しない。

### 採用判断の防波堤
将来「便利だから」を理由に以下を入れたくなったら、必ず本セクションに戻ること：
- rewrite / upsert / merge → 不採用（immutable を壊す）
- revision tracking → 不採用（"当時の認識" を上書きする）
- 集計・派生テーブル → 不採用（観測層と分析層を混ぜる）

### Data Invariants（仕様）

`(source, dataset, observed_date)` は常に **ちょうど 1 行**。

- Provider が同一 `observed_date` に複数行を返した場合は `DataIntegrityError` で write を中止する
- これは duplicate index / tz normalize 後 collapse / split adjustment 由来重複 などの
  「未来の静かな破壊」を検出するための invariant
- storage 層は write 直前にこの不変条件を必ず検査する

---

## 実装時に絶対落とさない 4 原則（v5 で追加）

実装フェーズで Claude Code が事故りやすい箇所。以下は必須:

1. **timezone normalize**: Provider 層で `observed_date` を必ず UTC 正規化
2. **schema 固定**: Pandas inference に任せず PyArrow schema を明示
3. **git pull --rebase**: push 前に必ず rebase（cron × workflow_dispatch の競合対策）
4. **exists 二段防御**: fetch 前の全日 exists チェック + fetch 後の行単位 exists チェック

---

## v7 → v8（最終実装前修正）

「時間概念」と「市場日付概念」の混同を完全に潰す。

| 項目 | v7 | v8 (採用) | 理由 |
|---|---|---|---|
| Yahoo の observed_date 抽出 | `to_datetime(utc=True).floor("D")` | **`tz_convert("America/NY").date`** | UTC floor だと NY session 日が UTC 日に化ける |
| FRED の observed_date 抽出 | UTC normalize 経由 | **`index.date` 直接** | FRED は元から日付概念 |
| `ingested_at` 定義 | "取得時刻"（曖昧） | **「`write_immutable()` 実行時の UTC」と明文化** | forensic 再現性 |
| storage API 入力型 | 暗黙 | **`_coerce_date()` で `datetime.date` に強制** | groupby 型揺れ吸収 |
| `git pull --rebase` | `--autostash` あり | **`--autostash` 削除** | CI で生成ファイル誤回収を起こす |
| 失敗判定 | 全失敗時のみ exit 1 | **error_ratio > 30% で exit 1** | 部分失敗の静かな蓄積を防ぐ |
| repo 肥大化注記 | 容量話のみ | **「archive system ではない」明記 + 退避先案内** | object 数爆発リスクを正しく説明 |

---

## v6 → v7（実務修正のみ）

v6 で設計の芯は完成。v7 は「個人運用で踏みうる罠」だけを潰す小修正のみ：

| 項目 | v6 | v7 (採用) | 理由 |
|---|---|---|---|
| `exists()` 仕様 | path 存在チェック | **健全な 1 行 parquet 判定** (corrupt/0 byte は False) | interrupted write による永久 skip 防止 |
| schema nullable | コメントのみ | **`nullable=True` を明示** | 可読性 |
| examples.sql 日付関数 | `CURRENT_TIMESTAMP` | **`CURRENT_DATE`** | `date32` と整合 |
| 検証期待値 | TIMESTAMP_NS 残骸 | **`DATE` に修正** | v5→v6 移行漏れ |

**追加で増やさないと決めたもの** (個人運用にはオーバーキル):
- semantic / metadata / manifest レイヤー
- revision system
- custom query abstraction

「小さいのに壊れにくい」が最重要。これ以上の抽象化は禁止。

---

## v1 → v6 の進化（ユーザーフィードバック反映）

| 項目 | v1 | v2 | v3 | v4 | v5 | v6 (採用) | 理由 |
|---|---|---|---|---|---|---|---|
| ファイル単位 | 月次 | 日次 immutable | → | → | → | → | rewrite なし・git 差分最小 |
| Hive path | `data.parquet` | `day=13.parquet` | **`day=13/data.parquet`** | → | → | → | Athena/Spark 移行性 |
| 重複対策 | upsert | 存在 skip | → | storage first | **二段防御** | → | 部分欠損対応 |
| **Invariant** | — | — | — | — | — | **`(source,dataset,observed_date)=1 row`、違反時 `DataIntegrityError`** | 静かな破壊検出 |
| `observed_date` 型 | `date` | `date32` | `timestamp[us, UTC]` | → | → | **`date32` に再帰** | 「カレンダーラベル」概念と型を一致 |
| Lookback | 30 日 | 当日 | today - 3 days | → | → | → | FRED 翌日更新・休場ズレ |
| Provider 責務 | — | abstract | → | → | **canonical schema 保証** | → | tz 揺れ吸収 |
| Parquet schema | inference | → | → | → | **PyArrow 明示固定** | → | drift 防止 |
| volume 型 | int64 | → | → | Int64 nullable | → | → | NaN 対応 |
| 原子書き込み | — | `rename` | → | `replace` | → | → | Windows 対応 |
| ログ保存 | JSON formatter | 全結果 commit | summary のみ | → | → | → | 肥大化回避 |
| summary ファイル名 | `_{date}.json` | → | → | → | → | **`_{ISO8601 ts}.json`** | cron×dispatch 同日衝突回避 |
| commit msg | 固定 | → | → | 情報付き | → | → | git log = 運用履歴 |
| push 競合 | — | — | — | — | `git pull --rebase` | → | 競合回避 |
| `.gitattributes` | — | — | — | — | `*.parquet binary` | → | git diff 削減 |
| revision tracking | — | — | README 明記 | → | → | → | 思想的に意図的 |
| `observed_date` 定義 | UTC truth | → | → | 市場提供元の日付 | → | → | NASDAQ 日跨ぎ |
| repo 肥大化 | — | — | — | README 案内 | → | → | clone 問題 |
| 設計思想 | 暗黙 | → | → | → | 認知履歴 | **`Git history = epistemic history`** + 採用判断防波堤 | 思想の完全閉合 |
| batch fetch | — | — | — | README 注記 | → | **設計負債として明記** | storage-first 前提と衝突 |
| concurrency | lock | 不要 | → | → | → | → | 低頻度 |

---

## ディレクトリ構造（完全 Hive 準拠）

```
data/
  source=fred/
    dataset=DGS10/year=2026/month=05/day=13/data.parquet
    dataset=DGS10/year=2026/month=05/day=14/data.parquet
    ...
  source=yahoo/
    dataset=QQQ/year=2026/month=05/day=13/data.parquet
    ...
logs/
  summary_2026-05-13.json   # 日次サマリ (counts only)
  summary_2026-05-17_backfill.json
.github/workflows/
  daily-ingest.yml
  weekly-backfill.yml
scripts/
  daily_ingest.py
  weekly_backfill.py
  providers/
    __init__.py
    base.py          # MarketDataProvider ABC
    fred.py
    yahoo.py
  storage.py         # immutable write + 存在チェック
  config.py          # データセット定義・定数
sql/
  examples.sql
reports/
  .gitkeep
requirements.txt
README.md
.gitignore
```

すべての partition key (`source`, `dataset`, `year`, `month`, `day`) を
ディレクトリ階層として保持。`data.parquet` が末端ファイル。
Athena / Spark / Polars scan / Delta / Iceberg への将来移行と互換。

---

## スキーマ

### 共通列
| 列 | 型 | 説明 |
|---|---|---|
| `observed_date` | **`date32`** | provider が返した market/session 営業日ラベル。時刻概念を持たない |
| `source` | string | `"fred"` / `"yahoo"` |
| `dataset` | string | `"DGS10"` / `"QQQ"` 等 |
| `ingested_at` | timestamp[us, UTC] | **`storage.write_immutable()` 実行直前** の UTC timestamp |

**v3→v5 で timestamp[us, UTC] を採用していたが、v6 で `date32` に戻す。**

概念整合性の問題: `observed_date` は **「市場提供元のカレンダーラベル」** であって
UTC truth ではない。時刻意味を持たない概念。
これを timestamp で持つと「時刻意味」を後付けで持ち始め、
README 定義（市場提供元の日付）と物理型の意味が乖離する。

TZ 落ち事故は「timestamp 採用の理由」ではなく
「Provider 層で datetime を適切に normalize する責務」で解決する：
- Provider 内部処理: tz-aware datetime で正確に扱う
- 保存層 (`date32`): 時刻概念を捨てたカレンダーラベルとして保存

`ingested_at` は本物の時刻概念を持つので `timestamp[us, UTC]` のまま。
**型 = 概念**の一致が思想整合性を担保する。

### FRED 固有
| 列 | 型 |
|---|---|
| `value` | float64 |

### Yahoo 固有
| 列 | 型 |
|---|---|
| `open` | float64 |
| `high` | float64 |
| `low` | float64 |
| `close` | float64 |
| `adj_close` | float64 |
| `volume` | **Int64 (nullable)** — 一部 ETF・取引停止日で NaN 来うる |

`observed_date` と `ingested_at` を完全分離する設計判断は重要。
「2026-05-13 時点で観測できた DGS10 の 2026-05-13 値」と
「2026-05-15 時点で改訂された同じ日の値」を将来区別したくなった場合、
ファイル単位で immutable に分かれているため、git 履歴を辿れば再現できる。
（今回はファイル一意性を `observed_date` で確保するため改訂は採用しないが、
将来は `ingested_at` 単位で別ファイルに分ける拡張が可能）

---

## コンポーネント設計

### `scripts/providers/base.py`
```python
class MarketDataProvider(ABC):
    name: str  # "fred" or "yahoo"

    @abstractmethod
    def fetch(self, dataset: str, start: date, end: date) -> pd.DataFrame:
        """canonical schema を保証した DataFrame を返す。
        - observed_date は timestamp[us, UTC] に正規化済み
        - 列名・型は providers/schema.py の定義通り
        """
```

**Provider 層の責務 = canonical schema の保証**。
yfinance の tz-aware/tz-naive 混在、FRED の date inference 揺れなどは
全て Provider 層で吸収し、上位層には常に同じ形の DataFrame を渡す。

このインタフェースを介すことで yfinance を将来 stooq / Tiingo に差し替え可能。

### Provider 設計原則（重要）

**`observed_date` は market/session label であって UTC truth ではない。**

UTC への `.floor("D")` は思想と実装が衝突する：
NASDAQ の 2026-05-13 session は UTC では 5/14 に跨ぐため、
UTC floor すると "market 5/13" が "UTC 5/14" になる事故が起きる。

各 Provider は **「provider が返した営業日ラベル」を保持** したまま `date` 型に変換する：

### `scripts/providers/fred.py`
- `fredapi.Fred` で取得
- `tenacity` で 3 回リトライ（exponential backoff, ネットワーク例外のみ）
- timeout 30 秒
- FRED は API 上 UTC 的 date を返すので `index.date` でそのまま `datetime.date` 化
- 出力: `observed_date (date), value (float64)` の DataFrame

### `scripts/providers/yahoo.py`
- `yfinance.download(ticker, start, end, auto_adjust=False, progress=False)`
- 同様にリトライ
- **市場 timezone を尊重する**:
  ```python
  # yfinance の index は tz-aware/naive が混在。一旦 NY market tz に揃え
  # てから session 日を抽出する。UTC floor はしない。
  if df.index.tz is None:
      idx = df.index.tz_localize("America/New_York")
  else:
      idx = df.index.tz_convert("America/New_York")
  observed_date = pd.Index(idx.date)  # market session date
  ```
- 列名を `open/high/low/close/adj_close/volume` に小文字スネークケース化
- `volume` は `Int64` (pandas nullable) にキャスト
- 出力: canonical schema 準拠の DataFrame（`observed_date` は `date`）

### `scripts/providers/schema.py`
**PyArrow schema を明示固定**（schema drift 防止）:
```python
import pyarrow as pa

FRED_SCHEMA = pa.schema([
    pa.field("observed_date", pa.date32()),
    pa.field("source",        pa.string()),
    pa.field("dataset",       pa.string()),
    pa.field("value",         pa.float64()),
    pa.field("ingested_at",   pa.timestamp("us", tz="UTC")),
])

YAHOO_SCHEMA = pa.schema([
    pa.field("observed_date", pa.date32()),
    pa.field("source",        pa.string()),
    pa.field("dataset",       pa.string()),
    pa.field("open",          pa.float64()),
    pa.field("high",          pa.float64()),
    pa.field("low",           pa.float64()),
    pa.field("close",         pa.float64()),
    pa.field("adj_close",     pa.float64()),
    pa.field("volume",        pa.int64(), nullable=True),  # 取引停止日で null
    pa.field("ingested_at",   pa.timestamp("us", tz="UTC")),
])
```
`storage.write_immutable()` はこの schema を必ず適用して書き出す。
Pandas inference に任せない。

Provider 層が `observed_date` を `date32` に変換して返す責務を負う。
yfinance の tz 揺れは Provider 内部で吸収し、最終的に時刻概念を捨てた
カレンダー日として上位層に渡す。

### `scripts/storage.py`
中核関数（3 つだけ）：
```python
from datetime import date
class DataIntegrityError(Exception): pass

# API 境界で型固定: 入力は必ず datetime.date に正規化する
def _coerce_date(d) -> date:
    if isinstance(d, date) and not isinstance(d, datetime):
        return d
    return pd.Timestamp(d).date()  # Timestamp/datetime64/str を吸収

def partition_path(source, dataset, observed_date: date) -> Path

def exists(source, dataset, observed_date) -> bool:
    """健全な 1 行 parquet が存在するかを判定。
    - ファイルが無い: False
    - 0 byte / 開けない / corrupt: False (破損とみなす)
    - row 数 != 1: False (invariant 違反のため再書き込みを許す)
    """
    path = partition_path(...)
    if not path.exists():
        return False
    try:
        meta = pq.read_metadata(path)
        return meta.num_rows == 1
    except Exception:
        return False  # corrupt / interrupted write は欠損扱い

def write_immutable(df, source, dataset, observed_date, schema: pa.Schema) -> bool
    # ── Invariant 検査（必須） ─────────────────────
    # (source, dataset, observed_date) = 常に 1 row
    if len(df) != 1:
        raise DataIntegrityError(
            f"Expected exactly 1 row for "
            f"({source}, {dataset}, {observed_date}), got {len(df)}"
        )
    # ────────────────────────────────────────────
    # 既に存在すれば False を返して skip
    # PyArrow schema を強制適用 (Pandas inference に任せない)
    # 一時ファイル → os.replace() で原子的書き込み（Windows でも安全）
    # source / dataset / ingested_at 列をここで付与
```

- **`exists()` は破損耐性を持つ**: interrupted write / 0 byte / parquet corruption
  で永久 skip されることを防ぐ。読めない or row 数違反は「無い」と同等とみなす
- **API 境界で型固定**: `partition_path` / `exists` / `write_immutable` の
  入口で `observed_date` を `datetime.date` に強制変換。
  groupby 経由で渡される `Timestamp` / `datetime64` / `np.datetime64` 等の
  型揺れを吸収し、path formatting の事故（`year=2026/month=5/day=13` vs
  `year=2026/month=05/day=13` 等）を防ぐ
- **Invariant 検査が write の入口**: 複数行があれば即エラー
  → "未来の静かな破壊" (duplicate / collapse) を発生時点で検出
- `os.rename` ではなく **`os.replace`** を使う（Windows での挙動差吸収）
- 書き込み時は schema を引数で受け取り `pa.Table.from_pandas(df, schema=schema)` で固定
- upsert ロジック・読み取りヘルパーは一切持たない
- 分析側は DuckDB が直接 Parquet を読むだけ

### `scripts/daily_ingest.py`
- 全 dataset を回す
- **取得期間 = `today - 3 days` ~ `today`**(FRED 翌日更新・休場ズレ対策)
- `today` の定義は `run_date_utc = datetime.now(timezone.utc).date()` だが、
  **実際の取得対象日は Provider 側の最新営業日に依存する**ことを README に明記。
  「JST 07:00 実行時点では NASDAQ 前営業日しか返らない」など。
- **exists 二段防御**:
  ```python
  # 第1段: fetch 前に全日 exists チェック → API を叩かない
  target_days = [today - timedelta(days=d) for d in range(0, 4)]
  if all(storage.exists(source, dataset, d) for d in target_days):
      continue

  # 第2段: fetch 後に行単位で exists チェック → 部分欠損に対応
  df = provider.fetch(dataset, target_days[-1], target_days[0])
  for observed_date, row_df in df.groupby("observed_date"):
      if storage.exists(source, dataset, observed_date):
          continue  # 既存日は触らない
      # write_immutable 内部で「len != 1 → DataIntegrityError」が発火
      storage.write_immutable(row_df, source, dataset, observed_date, schema)
  ```
  `DataIntegrityError` は dataset 単位で catch して errors に積み、他は継続。
  部分欠損（例: 5/10 だけ無い）状態でも正しく埋まる。
- 空 DataFrame（休場・未公開）はファイル作らず skip 扱い
- 例外は dataset 単位で握る
- 最後に **サマリのみ**を `logs/summary_{run_ts}.json` に書き出す。
  ファイル名は ISO8601 タイムスタンプ形式（例: `summary_20260513T220001Z.json`）
  → 同日 cron + workflow_dispatch 二重実行でもファイル名衝突しない:
  ```json
  {
    "run_at": "2026-05-13T22:00:01Z",
    "kind": "daily",
    "written": 12,
    "skipped_exists": 28,
    "skipped_empty": 2,
    "errors": [
      {"source": "yahoo", "dataset": "CPER", "error": "yfinance: rate limit"}
    ]
  }
  ```
  失敗時のみ詳細を保持。成功は件数だけ。長期間 commit し続けても肥大化しない。
- GitHub Actions log 自体には dataset 単位の詳細を全部出すので、
  当日中のデバッグはそれで十分。git に永続化するのはサマリだけ。
- **error ratio による fail 判定**:
  ```
  error_ratio = errors / total_datasets
  if error_ratio > 0.30:  # 閾値 30%
      exit 1
  ```
  部分失敗の "静かな蓄積" を防ぐ。rate limit / schema drift / provider behavior change
  が複数 dataset で同時に起きた場合は workflow を fail させて気付けるようにする。
  単発失敗（1-2 dataset のみ）は許容して exit 0。

### `scripts/weekly_backfill.py`
- 過去 30 日を走査
- 各 (source, dataset, day) について `storage.exists()` が False なら取得試行
- 結果サマリを `logs/summary_{run_ts}_backfill.json` に記録（タイムスタンプ形式）
- 平日のみ判定はしない（FRED/Yahoo が空を返せば自然に skip）

### `scripts/config.py`
```python
HTTP_TIMEOUT_SEC = 30
RETRY_ATTEMPTS = 3
RETRY_BASE_WAIT = 2  # exponential backoff

FRED_DATASETS = ["DGS2", "DGS10", "DGS30", "DFII10", "T10YIE",
                 "BAMLH0A0HYM2", "BAMLH0A0IGAA", "VIXCLS", "DEXUSEU"]
YAHOO_DATASETS = ["QQQ", "SPY", "RSP", "IWM", "GLD", "USO", "CPER"]

BACKFILL_LOOKBACK_DAYS = 30
```

### `.github/workflows/daily-ingest.yml`
- `schedule: '0 22 * * *'` (UTC 22:00 = JST 07:00, 米市場クローズ後)
- `workflow_dispatch:` 手動可
- ジョブ: checkout → Python setup → `pip install -r requirements.txt` →
  `python scripts/daily_ingest.py` → `git add data/ logs/` →
  **`git pull --rebase origin main`** → 変更があれば commit/push
  (`--autostash` は CI で生成ファイル誤回収を起こすため使わない)
- `permissions: contents: write` を必須付与
- FRED_API_KEY は secrets から
- 失敗時もログだけは commit されるよう、ingest exit code は git step 前に握る
- **push 競合対策**: cron と workflow_dispatch が同時実行された際の
  `non-fast-forward` を防ぐため、push 前に必ず rebase。
  immutable ファイルしか追加しないので rebase 衝突は構造的に発生しない。
- **commit message は情報を残す**:
  - 日次: `ingest: YYYY-MM-DD daily snapshot (N written, M errors)`
  - 週次: `backfill: YYYY-MM-DD weekly fill (N filled)`
  - `git log` だけで運用履歴が辿れるようにする

### `.github/workflows/weekly-backfill.yml`
- `schedule: '0 23 * * 0'` (毎週日曜 UTC 23:00)
- `workflow_dispatch:`
- ジョブ構成は daily と同形

### `requirements.txt`
```
pandas>=2.0
pyarrow>=15.0
fredapi>=0.5
yfinance>=0.2.40
tenacity>=8.2
requests>=2.31
```

### `sql/examples.sql`
```sql
-- DGS10 1 年分
SELECT observed_date, value
FROM read_parquet('data/source=fred/dataset=DGS10/**/*.parquet',
                  hive_partitioning = true)
WHERE observed_date >= CURRENT_DATE - INTERVAL 1 YEAR
ORDER BY observed_date;

-- 全 FRED を縦結合
SELECT * FROM read_parquet('data/source=fred/dataset=*/**/*.parquet',
                            hive_partitioning = true);

-- 10Y 金利 と QQQ 終値
SELECT f.observed_date, f.value AS dgs10, y.adj_close AS qqq
FROM read_parquet('data/source=fred/dataset=DGS10/**/*.parquet',
                  hive_partitioning = true) f
JOIN read_parquet('data/source=yahoo/dataset=QQQ/**/*.parquet',
                  hive_partitioning = true) y USING (observed_date)
ORDER BY f.observed_date DESC LIMIT 60;

-- 日次サマリの履歴
SELECT * FROM read_json_auto('logs/summary_*.json');
```

### `README.md`
構成:

**1. 設計思想（冒頭に置く・最重要）**
> このリポジトリの本体は市場データではなく **「認知履歴」** である。
> 観測時点での世界認識を保存する。後から修正された "真実" ではなく、
> その時点で自分に見えていた "真実" を残す。

**2. 何をする / 何をしない**
- する: 日次の immutable な観測値保存、DuckDB から読める Parquet レイク
- しない: 解析・通知・UI・revision tracking・intraday 保存

**3. セットアップ**: FRED API key を repository secret に設定

**4. ローカル実行**

**5. DuckDB で読む例**: `sql/examples.sql` への参照

**6. 設計原則**: v5 差分表をそのまま転載

**7. 明示する制約と注意事項**:
- **revision tracking を行わない**
  （`observed_date` あたり 1 行 immutable。FRED の事後改訂値は反映されない。
  これは設計思想に基づく意図的な選択であり、バグではない）
- **`observed_date` は「市場データ提供元の日付」**であり、UTC calendar truth ではない。
  NASDAQ の `2026-05-13` は UTC では翌日に跨ぐ場合がある。
  FRED と Yahoo の同一 `observed_date` で SQL JOIN する際は、
  この前提を踏まえて結果を解釈すること。
- **取得対象日は Provider 側ロジックに依存**
  JST 07:00 実行時点で前営業日のデータしか返らないケースがある。
  `weekly_backfill.py` が翌週に拾うため最終的には埋まる。
- OHLCV のうち時刻はバーの日付に限定。intraday は保存しない
- **Provider が canonical schema を保証する**
  yfinance の tz 揺れなどは Provider 層で吸収する設計。
- **`ingested_at` の固定定義**:
  本リポジトリ全体で `ingested_at` は **「`storage.write_immutable()` 実行時の UTC timestamp」** に固定する。
  fetch 開始時刻でも provider response 時刻でも commit 時刻でもない。
  この一意定義により、将来 forensic（「いつ書かれた値か」）が再現可能になる。
- **Provider behavior itself is not immutable**
  Provider (FRED API / yfinance) の挙動自体は時とともに変わる：split adjustment 仕様変更、
  auto_adjust デフォルト変更、tz 仕様変更、holiday handling 変更 など。
  本リポジトリは revision tracking を行わないため、
  「同じ `observed_date` が将来別の値で取得される」可能性は思想上許容する。
  未来の自分が「2026年5月と2028年5月で取得結果が違う？」と思った場合、
  この仕様により説明される。
- **Concurrent workflows may fail harmlessly due to immutable race**
  cron + workflow_dispatch + manual rerun が同時実行された場合、
  片方の `git push` が non-fast-forward で失敗する可能性がある。
  immutable ファイルしか追加しないため、失敗側は次回実行で自然に埋まる。
  lock は意図的に持たない（運用都合より思想を優先）。
- **このリポジトリは archive system ではない**
  active observation window を主目的とする。
  `1 day = 1 parquet` 設計は Git object 数を爆発的に増やすため、
  数年スケールで以下が起きる:
  - `git clone` の劇的低速化
  - `git gc` の長時間化
  - GitHub Actions checkout の遅延
  - packfile の巨大化

  超長期保存が必要になった時点で以下のいずれかへ退避する:
  - 年単位の archive リポジトリへ移動
  - object storage (S3 / R2 / GCS) へエクスポート
  - GitHub Release artifact として固定

  active window は直近 1〜3 年程度を想定。それより古いものは別保管が前提。
  当面の運用緩和策:
  - `git clone --depth 1` で shallow clone
  - `git clone --filter=blob:none` で partial clone
- **将来の最適化余地と既知の設計負債**:
  - 現在は `1 dataset = 1 HTTP call` だが、yfinance は複数ティッカーまとめて取得可能
  - dataset 数が大幅に増えた場合は batch fetch 最適化を検討
  - **ただし** 現在の "fetch 前 exists 全件チェック" 最適化は
    `1 dataset = 1 HTTP request` という暗黙前提に依存している
  - batch fetch を導入する際は、storage-first 最適化のロジック自体を
    再設計する必要がある（既知の設計負債）

### `.gitignore`
`.venv/`, `__pycache__/`, `*.pyc`, `.env`, `*.duckdb`

### `.gitattributes`
```
*.parquet binary
```
Parquet を binary 指定して `git diff` の無駄な処理を抑制。
GitHub UI でも `Binary file not shown` 表示になりレビュー時の負荷を下げる。

---

## 主要ファイル一覧

| パス | 役割 |
|---|---|
| `.github/workflows/daily-ingest.yml` | 日次自動実行 |
| `.github/workflows/weekly-backfill.yml` | 週次バックフィル |
| `scripts/daily_ingest.py` | 当日分取得エントリ |
| `scripts/weekly_backfill.py` | 欠損補完エントリ |
| `scripts/storage.py` | immutable write |
| `scripts/config.py` | データセット定義 |
| `scripts/providers/base.py` | Provider ABC |
| `scripts/providers/schema.py` | PyArrow schema 定義（drift 防止） |
| `scripts/providers/fred.py` | FRED Provider |
| `scripts/providers/yahoo.py` | Yahoo Provider |
| `requirements.txt` | 依存 |
| `README.md` | ドキュメント |
| `sql/examples.sql` | DuckDB サンプル |
| `.gitignore` | git 除外 |
| `.gitattributes` | Parquet を binary 指定 |
| `reports/.gitkeep` | 空ディレクトリ保持 |

---

## 検証方法（end-to-end）

1. **ローカル動作確認**
   ```bash
   pip install -r requirements.txt
   export FRED_API_KEY=xxx
   python scripts/daily_ingest.py
   ```
   生成確認:
   - `data/source=fred/dataset=DGS10/year=2026/month=05/day=13/data.parquet`
   - `data/source=yahoo/dataset=QQQ/year=2026/month=05/day=13/data.parquet`
   - `logs/summary_2026-05-13.json`

2. **冪等性確認**
   同じ日に 2 回実行 → 2 回目は全 dataset で「exists 第1段で skip」
   → Git diff: 変更なし

3. **二段防御の確認**
   - 第1段: 全日 exists 済み dataset では fetch が呼ばれないことを log で確認
   - 第2段: `data/` の任意の中間日（例 5/11 のみ）を削除 → daily_ingest 実行
     → 5/11 のみが復元され、他の日は再書き込みされないことを確認

4. **バックフィル確認**
   `data/` の任意の日付ファイルを削除 → `weekly_backfill.py` 実行
   → 同ファイルが復元されることを確認

5. **スキーマ確認**
   ```bash
   duckdb -c "DESCRIBE SELECT * FROM read_parquet('data/source=fred/dataset=DGS10/**/*.parquet', hive_partitioning=true)"
   ```
   期待: `observed_date, source, dataset, value, ingested_at, year, month, day`
   （末尾 3 つは Hive partition 由来）
   - `observed_date` の型が `DATE` であること
   - `ingested_at` の型が `TIMESTAMP WITH TIME ZONE` ないし `TIMESTAMP_NS` であること
   - 複数日のファイル間で型が揺れていないこと

6. **失敗ログ確認**
   一時的に FRED_API_KEY を不正値にして実行 →
   `logs/summary_*.json` の `errors` 配列にエラーが記録されることを確認

7. **Invariant 検査の発火確認**
   ユニットテストで storage.write_immutable() に 2 行の DataFrame を渡し、
   `DataIntegrityError` が raise されることを確認。
   `data/` には何も書き込まれないこと（trial write 前にエラーで止まる）

8. **GitHub Actions 動作確認**
   - `workflow_dispatch` で手動実行
   - data/ と logs/ への commit が走ることを確認
   - `git pull --rebase` step が実行されることを確認
   - summary ファイル名にタイムスタンプが入っていることを確認

9. **DuckDB クエリ**
   `sql/examples.sql` の各クエリが結果を返す

---

## やらないこと（要件 + フィードバック）

- AI 分析・予測・signal 生成
- ダッシュボード・WebUI
- アラート通知
- 取引ロジック
- データ品質スコアリング
- **upsert / merge / read-modify-write 系の処理一切**
- **JSON 構造化ロガー**（標準 logging で十分）
- **DuckDB ヘルパー / 集計レイヤー**（SQL examples のみ）
- **concurrency lock**（日次低頻度なら不要）
- **revision tracking**（FRED 改訂値の追跡。README に明記）
- **manifest.json / sha256 検証**（壊れず 1 ヶ月回ることが先決。将来必要なら追加）
