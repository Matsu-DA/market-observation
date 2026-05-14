"""Daily ingest entry point.

Fetches the last DAILY_LOOKBACK_DAYS for each dataset and writes any
observed_date that doesn't already exist. Exits non-zero only if the
error ratio exceeds ERROR_RATIO_THRESHOLD.
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone

from scripts.config import DAILY_LOOKBACK_DAYS, ERROR_RATIO_THRESHOLD
from scripts.ingest import run_ingest


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    log = logging.getLogger("daily_ingest")

    today_utc = datetime.now(timezone.utc).date()
    result = run_ingest(
        kind="daily",
        reference=today_utc,
        lookback_days=DAILY_LOOKBACK_DAYS,
    )

    ratio = result.error_ratio()
    log.info(
        "done written=%d skipped_exists=%d skipped_empty=%d errors=%d ratio=%.2f",
        result.written, result.skipped_exists, result.skipped_empty,
        len(result.errors), ratio,
    )

    if ratio > ERROR_RATIO_THRESHOLD:
        log.error("error_ratio_exceeded ratio=%.2f threshold=%.2f",
                  ratio, ERROR_RATIO_THRESHOLD)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
