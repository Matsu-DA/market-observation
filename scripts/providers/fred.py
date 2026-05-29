"""FRED provider.

observed_date is taken directly from the FRED API index (already a calendar date).
"""
from __future__ import annotations

import os
from datetime import date

import pandas as pd
import requests
from fredapi import Fred
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from scripts.config import HTTP_TIMEOUT_SEC, RETRY_ATTEMPTS, RETRY_BASE_WAIT
from scripts.providers.base import MarketDataProvider

_FRED_PROBE_URL = "https://api.stlouisfed.org/fred/series"


class FredApiError(RuntimeError):
    """Raised when a FRED fetch fails, carrying a human-readable cause.

    fredapi collapses any HTTP error it cannot parse into ``ValueError(None)``,
    which stringifies to the useless ``"ValueError: None"``. This type replaces
    that with the real reason (e.g. an HTTP 504 gateway outage).
    """


class FredProvider(MarketDataProvider):
    name = "fred"

    def __init__(self, api_key: str | None = None):
        api_key = api_key or os.environ.get("FRED_API_KEY")
        if not api_key:
            raise RuntimeError("FRED_API_KEY is not set")
        self._api_key = api_key
        self._fred = Fred(api_key=api_key)
        self._diag: str | None = None

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

    def _diagnose(self) -> str:
        """One-shot, memoized probe of the FRED API to name the real failure.

        Memoized so a run where every series fails (a source-wide outage) issues
        a single diagnostic request rather than one per dataset.
        """
        if self._diag is not None:
            return self._diag
        params = {"series_id": "DGS10", "api_key": self._api_key, "file_type": "json"}
        try:
            resp = requests.get(
                _FRED_PROBE_URL, params=params, timeout=min(HTTP_TIMEOUT_SEC, 10)
            )
        except requests.Timeout:
            self._diag = "connection timed out"
        except requests.RequestException as exc:
            self._diag = f"connection error: {type(exc).__name__}"
        else:
            if resp.status_code == 200:
                self._diag = "HTTP 200 (API reachable)"
            else:
                self._diag = f"HTTP {resp.status_code} {(resp.reason or '').strip()}".strip()
        return self._diag

    def fetch(self, dataset: str, start: date, end: date) -> pd.DataFrame:
        try:
            series = self._get_series(dataset, start, end)
        except ValueError as exc:
            detail = str(exc).strip()
            if not detail or detail == "None":
                # fredapi could not parse FRED's HTTP error body — the signature of
                # a non-standard error such as a 5xx gateway timeout. Name the cause.
                raise FredApiError(
                    f"FRED API unreachable ({self._diagnose()}); dataset={dataset}"
                ) from exc
            raise FredApiError(f"FRED API error for {dataset}: {detail}") from exc

        if series is None or len(series) == 0:
            return pd.DataFrame(columns=["observed_date", "value"])

        df = series.rename("value").reset_index().rename(columns={"index": "observed_date"})
        df = df.dropna(subset=["observed_date"])
        df["observed_date"] = df["observed_date"].apply(
            lambda x: pd.Timestamp(x).date()
        )
        df["value"] = pd.to_numeric(df["value"], errors="coerce")
        return df[["observed_date", "value"]].reset_index(drop=True)
