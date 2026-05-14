from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
LOGS_DIR = REPO_ROOT / "logs"

HTTP_TIMEOUT_SEC = 30
RETRY_ATTEMPTS = 3
RETRY_BASE_WAIT = 2

DAILY_LOOKBACK_DAYS = 3
BACKFILL_LOOKBACK_DAYS = 30

ERROR_RATIO_THRESHOLD = 0.30

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
