"""Weekly backfill entry point.

Scans BACKFILL_LOOKBACK_DAYS back from today and fills any missing
observed_date. Same exists-skip behavior as daily ingest.
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone

from scripts.config import BACKFILL_LOOKBACK_DAYS, ERROR_RATIO_THRESHOLD
from scripts.ingest import run_ingest


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    log = logging.getLogger("weekly_backfill")

    today_utc = datetime.now(timezone.utc).date()
    result = run_ingest(
        kind="backfill",
        reference=today_utc,
        lookback_days=BACKFILL_LOOKBACK_DAYS,
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
