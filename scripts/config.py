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
    "DGS2",
    "DGS10",
    "DGS30",
    "DFII10",
    "T10YIE",
    "BAMLH0A0HYM2",
    "BAMLH0A0IGAA",
    "VIXCLS",
    "DEXUSEU",
]

YAHOO_DATASETS = [
    "QQQ",
    "SPY",
    "RSP",
    "IWM",
    "GLD",
    "USO",
    "CPER",
]
