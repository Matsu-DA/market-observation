# config.py 観測対象拡張計画 (v2 / レビュー反映)

## Context

現在の観測対象は 9 FRED + 7 Yahoo = 16 dataset。
本番初回実行で構成上の偏りが明確になった:

- 金利・インフレ: 強い
- 信用収縮: 最低限
- **流動性 / ドル資金調達**: 観測手段が無い
- **市場内部崩壊 (HY ETF / TLT / 地銀)**: 観測手段が無い
- **AI/半導体サイクル**: 観測手段が無い
- **広範コモディティ / エネルギー株 / 金融セクター**: 観測が薄い

加えて初回実行で `BAMLH0A0IGAA` が "series does not exist" エラー
（FRED に存在しない ID）。これも修正する。

目的: **「価格ウォッチ」から「市場構造観測」への進化**。
特に "壊れ始め" を早期検知できる layer を追加する。

immutable 設計のため、削除 dataset (`DEXUSEU`) の既存 parquet は
git 履歴に残ったまま (rewrite しない)。新規 fetch を停止するだけ。

---

## レビューで反映した重要判断

1. **`^MOVE` は採用見送り (comment out 保持)**
   yfinance での MOVE は突然取得不能 / 空 / volume 型揺れ等が起きやすい。
   "epistemic history" の純度を保つため、不安定 provider をコア観測に入れない。
   将来 polygon.io 等の安定 source を入れた時点で復活させる伏線として
   コード上にコメント保持する。

2. **`XLF` / `KRE` 追加**: 金融セクターは「最後に壊れる」のではなく
   「最初にヒビが入る」。特に KRE は地銀ストレス / CRE / deposit flight /
   funding stress を先行的に映す (2023 年 SVB 系の典型)。

3. **HYG / JNK 二重持ち維持**: redundancy ではなく、両者の乖離自体が
   ETF liquidity stress / creation-redemption dysfunction の観測対象。

4. **T10Y2Y 維持**: 自分で計算可能だが、"当時 FRED が返した値" を保存する
   ことに意味がある (FRED 側の calc method / holiday handling が将来変わる
   可能性に備える immutable epistemology)。

5. **CPER 残置**: DBC と重複するが copper/gold ratio 観測用に保持。

6. **README に観測カテゴリを追記**: dataset 一覧は単なる設定ではなく
   観測哲学そのもの。なぜ SOFR を見るか / なぜ KRE を見るか は
   未来の自分が忘れる。最低限のカテゴリ説明を README に残す。

---

## 変更対象ファイル

| ファイル | 変更内容 |
|---|---|
| `scripts/config.py` | FRED_DATASETS と YAHOO_DATASETS を全面差し替え |
| `README.md` | 「観測カテゴリ」セクションを追加 |

Provider / schema / storage は **一切変更不要**。

---

## 新 FRED_DATASETS (9 → 13 件)

```python
FRED_DATASETS = [
    # Rates
    "DGS2",
    "DGS10",
    "DGS30",
    "DFII10",
    "T10YIE",
    "T10Y2Y",        # new(2026-05-14): 2s10s curve

    # Credit
    "BAMLH0A0HYM2",
    "BAMLC0A0CM",    # new(2026-05-14): IG Corporate OAS (BAMLH0A0IGAA 不存在のため差替)

    # Vol / Stress
    "VIXCLS",

    # Liquidity / Funding
    "SOFR",          # new(2026-05-14): O/N 担保調達金利
    "DFF",           # new(2026-05-14): Fed Funds Effective
    "RRPONTSYD",     # new(2026-05-14): O/N Reverse Repo (TGA再構築/QT/funding stress 直結)

    # Dollar
    "DTWEXBGS",      # new(2026-05-14): Broad Dollar Index (世界流動性 / EM圧迫 / dollar shortage)
    # removed(2026-05-14): DEXUSEU (EUR 単独は優先度低、Broad Dollar で代替)
]
```

## 新 YAHOO_DATASETS (7 → 15 件)

```python
YAHOO_DATASETS = [
    # Equity index
    "SPY",
    "QQQ",
    "RSP",
    "IWM",

    # Credit ETF
    "HYG",           # new(2026-05-14): HY ETF (HYG/SPY, HYG/IEF ratios)
    "JNK",           # new(2026-05-14): HY ETF (HYG との乖離自体が dislocation シグナル)

    # Rates ETF
    "TLT",           # new(2026-05-14): 20+ year Treasury (duration squeeze 観測)

    # AI / Semis
    "SOXX",          # new(2026-05-14): 半導体 ETF (AI CAPEX 上流)

    # Financial sector
    "XLF",           # new(2026-05-14): 金融セクター
    "KRE",           # new(2026-05-14): 地銀 ETF (CRE / deposit flight / funding stress 先行)

    # Commodities
    "GLD",
    "USO",
    "CPER",          # 維持: copper/gold ratio 用
    "DBC",           # new(2026-05-14): 広範コモディティ (CPER の中国依存ノイズを補完)

    # Energy equities
    "XLE",           # new(2026-05-14): エネルギー株 (USO の先物構造ノイズを補完)

    # Vol (見送り)
    # "^MOVE",       # 採用見送り(2026-05-14): yfinance での MOVE 安定性が低い。
    #                  安定 source を入れた時点で復活させる
]
```

## 構造的サマリ

| 軸 | 旧 | 新 |
|---|---|---|
| 金利 | DGS×3 + DFII10 + T10YIE | + T10Y2Y |
| 信用 (FRED) | HY OAS のみ | + IG OAS |
| 信用 (ETF) | 無し | HYG + JNK |
| 流動性 | 無し | SOFR + DFF + RRPONTSYD |
| ドル | DEXUSEU (削除) | DTWEXBGS |
| 株式 | SPY/QQQ/RSP/IWM | + TLT |
| AI/半導体 | 無し | SOXX |
| 金融セクター | 無し | XLF + KRE |
| ボラ | VIX のみ | (^MOVE は見送り) |
| 商品 | GLD/USO/CPER | + DBC |
| エネ株 | 無し | XLE |
| **合計** | **16** | **28** |

## README 追記内容 (概略)

`## 観測カテゴリ` セクションを `## データソース` 直下に追加。

```markdown
## 観測カテゴリ

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
```

短く、なぜ見るかの 1 行ずつ。これ以上 detailed にすると認知履歴ではなく
教科書になるため抑制。

---

## 既知の運用上の注意

1. **DEXUSEU の運用停止**: 既存 parquet は git に残置 (immutable)。新規 fetch されない。
2. **新 dataset の過去データ**: 次回 `weekly_backfill` 実行で 30 日分自動充填。
3. **SOFR の歴史開始**: 2018 年〜。30 日 backfill 範囲なら全期間取得可。
4. **error_ratio 30% 閾値**: 28 dataset で許容失敗 = 8。新規 11 件のうち
   半数 ID typo でも fail しない安全マージン。
5. **`^MOVE` 再活性化のタイミング**: yfinance 以外の安定 provider (polygon.io,
   stooq, Tiingo 等) を Provider 層に追加した時点で復活。それまでは
   コメント保持で「設計意図」を文書化。

## 既存データの扱い (immutable 思想)

- `data/source=fred/dataset=DEXUSEU/...` の既存 parquet: **削除しない**
- 新 dataset の初回 daily 実行: 3 日 lookback × 12 新規 = 最大 36 件書き込み増

---

## 検証手順

### Step 1: ローカル単体検証 (FRED_API_KEY 必要)
新規 series ID / ticker を 1 件ずつ叩いて存在確認:
```bash
export FRED_API_KEY=...
python -c "
from scripts.providers.fred import FredProvider
from scripts.providers.yahoo import YahooProvider
from datetime import date, timedelta
end = date.today(); start = end - timedelta(days=5)
new_fred = ['T10Y2Y','BAMLC0A0CM','SOFR','DFF','RRPONTSYD','DTWEXBGS']
new_yahoo = ['HYG','JNK','TLT','SOXX','XLF','KRE','DBC','XLE']
for d in new_fred:
    try: print(f'FRED {d}: {len(FredProvider().fetch(d,start,end))} rows')
    except Exception as e: print(f'FRED {d}: ERROR {e}')
for t in new_yahoo:
    try: print(f'YAHOO {t}: {len(YahooProvider().fetch(t,start,end))} rows')
    except Exception as e: print(f'YAHOO {t}: ERROR {e}')
"
```
失敗 ID があればこの段階で plan に戻して差し替え。

### Step 2: commit + push + 手動 workflow_dispatch
```
1. config.py / README.md をコミット (1 PR / 1 commit)
2. Actions → daily-ingest → Run workflow
3. summary JSON で error_ratio が 0 (or 30% 未満) を確認
4. 新規 parquet が data/source=*/dataset=新ID/ 配下に生成されたことを確認
```

### Step 3: weekly_backfill 手動実行で過去 30 日充填
```
Actions → weekly-backfill → Run workflow
→ 新規 dataset 12 件 × 30 日 ≒ 360 ファイル追加
```

---

## やらないこと

- Provider / schema / storage の変更
- DEXUSEU の過去データ削除 (immutable)
- `^MOVE` 等のため schema を変える
- VIXCLS の削除 (格下げだが保持)
- batch fetch 最適化
- README に dataset ごとの詳細解説 (1 行カテゴリ説明に留める)
