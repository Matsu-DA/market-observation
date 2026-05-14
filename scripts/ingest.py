"""Shared ingest pipeline used by daily_ingest.py and weekly_backfill.py.

Two-stage exists defense:
  1. Before fetch: if every target date already has a healthy parquet,
     skip the HTTP call entirely.
  2. After fetch: for each row returned, re-check exists() per
     observed_date so partial gaps are filled without rewriting healthy files.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

import pandas as pd

from scripts import storage
from scripts.config import (
    ERROR_RATIO_THRESHOLD,
    FRED_DATASETS,
    LOGS_DIR,
    YAHOO_DATASETS,
)
from scripts.providers.base import MarketDataProvider
from scripts.providers.fred import FredProvider
from scripts.providers.schema import SCHEMAS
from scripts.providers.yahoo import YahooProvider

log = logging.getLogger(__name__)


@dataclass
class IngestResult:
    kind: str
    run_at: str
    written: int = 0
    skipped_exists: int = 0
    skipped_empty: int = 0
    errors: list[dict] = field(default_factory=list)
    total_datasets: int = 0

    def as_dict(self) -> dict:
        return {
            "run_at": self.run_at,
            "kind": self.kind,
            "written": self.written,
            "skipped_exists": self.skipped_exists,
            "skipped_empty": self.skipped_empty,
            "errors": self.errors,
            "total_datasets": self.total_datasets,
        }

    def error_ratio(self) -> float:
        if self.total_datasets == 0:
            return 0.0
        return len(self.errors) / self.total_datasets


def _build_providers() -> dict[str, MarketDataProvider]:
    return {
        "fred": FredProvider(),
        "yahoo": YahooProvider(),
    }


def _target_days(reference: date, lookback_days: int) -> list[date]:
    return [reference - timedelta(days=d) for d in range(0, lookback_days + 1)]


def _ingest_dataset(
    provider: MarketDataProvider,
    dataset: str,
    target_days: list[date],
    result: IngestResult,
) -> None:
    source = provider.name
    schema = SCHEMAS[source]

    if all(storage.exists(source, dataset, d) for d in target_days):
        result.skipped_exists += len(target_days)
        log.info("skip_fetch source=%s dataset=%s reason=all_exist", source, dataset)
        return

    start = min(target_days)
    end = max(target_days)
    try:
        df = provider.fetch(dataset, start, end)
    except Exception as exc:
        msg = f"{type(exc).__name__}: {exc}"
        log.warning("fetch_error source=%s dataset=%s err=%s", source, dataset, msg)
        result.errors.append({"source": source, "dataset": dataset, "error": msg})
        return

    if df is None or df.empty:
        result.skipped_empty += 1
        log.info("empty source=%s dataset=%s", source, dataset)
        return

    target_set = {d for d in target_days}
    df = df[df["observed_date"].isin(target_set)]
    if df.empty:
        result.skipped_empty += 1
        log.info("no_target_days source=%s dataset=%s", source, dataset)
        return

    for observed_date, row_df in df.groupby("observed_date", sort=True):
        try:
            if storage.exists(source, dataset, observed_date):
                result.skipped_exists += 1
                continue
            wrote = storage.write_immutable(
                row_df, source, dataset, observed_date, schema
            )
            if wrote:
                result.written += 1
                log.info(
                    "wrote source=%s dataset=%s observed_date=%s",
                    source, dataset, observed_date,
                )
        except Exception as exc:
            msg = f"{type(exc).__name__}: {exc}"
            log.error(
                "write_error source=%s dataset=%s observed_date=%s err=%s",
                source, dataset, observed_date, msg,
            )
            result.errors.append(
                {
                    "source": source,
                    "dataset": dataset,
                    "observed_date": str(observed_date),
                    "error": msg,
                }
            )


def run_ingest(
    kind: str,
    reference: date,
    lookback_days: int,
    dataset_filter: Iterable[str] | None = None,
) -> IngestResult:
    now = datetime.now(timezone.utc)
    result = IngestResult(kind=kind, run_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"))

    providers = _build_providers()
    targets = _target_days(reference, lookback_days)

    plan: list[tuple[MarketDataProvider, str]] = []
    for ds in FRED_DATASETS:
        if dataset_filter and ds not in dataset_filter:
            continue
        plan.append((providers["fred"], ds))
    for ds in YAHOO_DATASETS:
        if dataset_filter and ds not in dataset_filter:
            continue
        plan.append((providers["yahoo"], ds))
    result.total_datasets = len(plan)

    for provider, dataset in plan:
        _ingest_dataset(provider, dataset, targets, result)

    _write_summary(result, now, kind)
    return result


def _write_summary(result: IngestResult, run_ts: datetime, kind: str) -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    ts = run_ts.strftime("%Y%m%dT%H%M%SZ")
    suffix = f"_{kind}" if kind != "daily" else ""
    path = LOGS_DIR / f"summary_{ts}{suffix}.json"
    path.write_text(json.dumps(result.as_dict(), indent=2, sort_keys=True))
    log.info("summary_written path=%s", path)
