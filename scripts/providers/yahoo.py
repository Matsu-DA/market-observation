"""Yahoo Finance provider.

observed_date is the NY market session date, NOT a UTC calendar date.
yfinance returns timestamps that may or may not be tz-aware; this provider
normalizes them to America/New_York and takes .date() so each row reflects
the session it belongs to.
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import yfinance as yf
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from scripts.config import RETRY_ATTEMPTS, RETRY_BASE_WAIT
from scripts.providers.base import MarketDataProvider

_COLUMN_MAP = {
    "Open": "open",
    "High": "high",
    "Low": "low",
    "Close": "close",
    "Adj Close": "adj_close",
    "Volume": "volume",
}


class YahooProvider(MarketDataProvider):
    name = "yahoo"

    @retry(
        retry=retry_if_exception_type((ConnectionError, TimeoutError, OSError)),
        stop=stop_after_attempt(RETRY_ATTEMPTS),
        wait=wait_exponential(multiplier=RETRY_BASE_WAIT, min=1, max=30),
        reraise=True,
    )
    def _download(self, ticker: str, start: date, end: date) -> pd.DataFrame:
        return yf.download(
            ticker,
            start=start.isoformat(),
            end=(end + timedelta(days=1)).isoformat(),
            auto_adjust=False,
            progress=False,
            threads=False,
        )

    def fetch(self, dataset: str, start: date, end: date) -> pd.DataFrame:
        raw = self._download(dataset, start, end)
        if raw is None or raw.empty:
            return pd.DataFrame(
                columns=["observed_date", "open", "high", "low",
                         "close", "adj_close", "volume"]
            )

        if isinstance(raw.columns, pd.MultiIndex):
            raw = raw.xs(dataset, axis=1, level=-1)

        idx = raw.index
        if isinstance(idx, pd.DatetimeIndex):
            if idx.tz is None:
                idx = idx.tz_localize("America/New_York", nonexistent="shift_forward",
                                      ambiguous="NaT")
            else:
                idx = idx.tz_convert("America/New_York")
            observed_date = pd.Index([d.date() if d is not pd.NaT else None for d in idx])
        else:
            observed_date = pd.Index([pd.Timestamp(d).date() for d in idx])

        df = raw.rename(columns=_COLUMN_MAP).copy()
        df["observed_date"] = observed_date
        df = df.dropna(subset=["observed_date"])

        for col in ("open", "high", "low", "close", "adj_close"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
            else:
                df[col] = pd.NA

        if "volume" in df.columns:
            df["volume"] = pd.to_numeric(df["volume"], errors="coerce").astype("Int64")
        else:
            df["volume"] = pd.Series([pd.NA] * len(df), dtype="Int64")

        return df[
            ["observed_date", "open", "high", "low", "close", "adj_close", "volume"]
        ].reset_index(drop=True)
