"""FRED provider.

observed_date is taken directly from the FRED API index (already a calendar date).
"""
from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from datetime import date
from urllib.error import HTTPError

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

    fredapi collapses HTTP errors into ValueError (or lets ET.ParseError escape
    when the response body is not valid XML). The original urllib HTTPError is
    preserved in __context__ and used to classify transient vs. permanent faults.
    """


class FredTransientError(FredApiError):
    """Transient fault that should be retried (HTTP 5xx or 429).

    Raised inside _get_series so tenacity can intercept it before the
    exception propagates to fetch().
    """


def _find_http_error(exc: BaseException) -> HTTPError | None:
    """Walk the exception chain and return the first urllib HTTPError found.

    Traversal order: __cause__ first (explicit ``raise X from Y``); falls back
    to __context__ only when __suppress_context__ is False (i.e. the chain has
    not been severed by ``raise X from None``). A depth cap of 20 guards against
    circular references.
    """
    visited: set[int] = set()
    current: BaseException | None = exc
    depth = 0
    while current is not None and depth < 20:
        obj_id = id(current)
        if obj_id in visited:
            break
        visited.add(obj_id)
        if isinstance(current, HTTPError):
            return current
        if current.__cause__ is not None:
            current = current.__cause__
        elif not current.__suppress_context__ and current.__context__ is not None:
            current = current.__context__
        else:
            break
        depth += 1
    return None


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
        retry=retry_if_exception_type(
            (ConnectionError, TimeoutError, OSError, FredTransientError)
        ),
        stop=stop_after_attempt(RETRY_ATTEMPTS),
        wait=wait_exponential(multiplier=RETRY_BASE_WAIT, min=1, max=30),
        reraise=True,
    )
    def _get_series(self, dataset: str, start: date, end: date) -> pd.Series:
        # fredapi converts HTTP errors to ValueError (or leaks ET.ParseError for
        # non-XML bodies). The original HTTPError is available via __context__ when
        # the chain is intact; _find_http_error extracts it for status-based routing.
        try:
            return self._fred.get_series(
                dataset,
                observation_start=start,
                observation_end=end,
            )
        except (ValueError, ET.ParseError) as exc:
            http = _find_http_error(exc)
            if http is not None:
                if http.code >= 500 or http.code == 429:
                    raise FredTransientError(
                        f"FRED HTTP {http.code} for {dataset}"
                    ) from exc
                # 4xx with a known HTTP status — not transient, let it propagate
                raise
            if isinstance(exc, ValueError):
                detail = str(exc).strip()
                if not detail or detail == "None":
                    # Fallback: fredapi produced a valueless ValueError with no HTTP
                    # chain. Treat as transient — this is a last-resort safety net in
                    # case fredapi is changed to sever the chain (e.g. raise ... from
                    # None), so we don't silently drop retryable outage errors.
                    raise FredTransientError(
                        f"FRED API unreachable (no detail) for {dataset}"
                    ) from exc
                # Descriptive ValueError (e.g. series not found) — not transient
                raise
            # ParseError without an HTTP backing — could be an API schema change or
            # a permanent XML inconsistency. Only retry when HTTP status confirms a
            # transient fault; without that, propagate unchanged.
            raise

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
        except FredTransientError as exc:
            raise FredApiError(
                f"FRED API unreachable ({self._diagnose()}); dataset={dataset}"
            ) from exc
        except (ValueError, ET.ParseError) as exc:
            detail = str(exc).strip()
            if not detail or detail == "None":
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
