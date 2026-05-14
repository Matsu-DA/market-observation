"""FRED provider.

observed_date is taken directly from the FRED API index (already a calendar date).
"""
from __future__ import annotations

import os
from datetime import date

import pandas as pd
from fredapi import Fred
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from scripts.config import HTTP_TIMEOUT_SEC, RETRY_ATTEMPTS, RETRY_BASE_WAIT
from scripts.providers.base import MarketDataProvider


class FredProvider(MarketDataProvider):
    name = "fred"

    def __init__(self, api_key: str | None = None):
        api_key = api_key or os.environ.get("FRED_API_KEY")
        if not api_key:
            raise RuntimeError("FRED_API_KEY is not set")
        self._fred = Fred(api_key=api_key)

    @retry(
        retry=retry_if_exception_type((ConnectionError, TimeoutError, OSError)),
        stop=stop_after_attempt(RETRY_ATTEMPTS),
        wait=wait_exponential(multiplier=RETRY_BASE_WAIT, min=1, max=30),
        reraise=True,
    )
    def _get_series(self, dataset: str, start: date, end: date) -> pd.Series:
        return self._fred.get_series(
            dataset,
            observation_start=start,
            observation_end=end,
        )

    def fetch(self, dataset: str, start: date, end: date) -> pd.DataFrame:
        series = self._get_series(dataset, start, end)
        if series is None or len(series) == 0:
            return pd.DataFrame(columns=["observed_date", "value"])

        df = series.rename("value").reset_index().rename(columns={"index": "observed_date"})
        df = df.dropna(subset=["observed_date"])
        df["observed_date"] = df["observed_date"].apply(
            lambda x: pd.Timestamp(x).date()
        )
        df["value"] = pd.to_numeric(df["value"], errors="coerce")
        return df[["observed_date", "value"]].reset_index(drop=True)
