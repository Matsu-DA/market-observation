"""Tests for scripts.providers.yahoo.YahooProvider."""
from __future__ import annotations

import datetime

import pandas as pd

from scripts.providers.yahoo import YahooProvider


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_OHLCV_COLS = ["Open", "High", "Low", "Close", "Adj Close", "Volume"]
_CANONICAL_COLS = ["observed_date", "open", "high", "low", "close", "adj_close", "volume"]
_DATES = [datetime.date(2026, 1, 5), datetime.date(2026, 1, 6)]


def _make_raw(dates, tz=None, multiindex=False) -> pd.DataFrame:
    """Build a raw DataFrame similar to what yf.download returns."""
    idx = pd.DatetimeIndex(
        [pd.Timestamp(d) for d in dates],
        name="Date",
    )
    if tz is not None:
        idx = idx.tz_localize(tz)

    data = {
        "Open": [100.0, 101.0],
        "High": [105.0, 106.0],
        "Low": [99.0, 100.0],
        "Close": [103.0, 104.0],
        "Adj Close": [103.0, 104.0],
        "Volume": [1_000_000, 1_100_000],
    }
    df = pd.DataFrame(data, index=idx)

    if multiindex:
        df.columns = pd.MultiIndex.from_product(
            [["Open", "High", "Low", "Close", "Adj Close", "Volume"], ["SPY"]]
        )

    return df


def _make_provider(monkeypatch, raw_df: pd.DataFrame) -> YahooProvider:
    provider = YahooProvider()
    monkeypatch.setattr(provider, "_download", lambda *a, **k: raw_df)
    return provider


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestYahooFetch:
    def test_tz_naive_index(self, monkeypatch):
        raw = _make_raw(_DATES, tz=None)
        provider = _make_provider(monkeypatch, raw)
        df = provider.fetch("SPY", _DATES[0], _DATES[-1])

        assert list(df.columns) == _CANONICAL_COLS
        assert list(df["observed_date"]) == _DATES

    def test_tz_aware_new_york(self, monkeypatch):
        raw = _make_raw(_DATES, tz="America/New_York")
        provider = _make_provider(monkeypatch, raw)
        df = provider.fetch("SPY", _DATES[0], _DATES[-1])

        assert list(df["observed_date"]) == _DATES

    def test_multiindex_columns_flattened(self, monkeypatch):
        raw = _make_raw(_DATES, multiindex=True)
        provider = _make_provider(monkeypatch, raw)
        df = provider.fetch("SPY", _DATES[0], _DATES[-1])

        assert list(df.columns) == _CANONICAL_COLS
        assert len(df) == 2

    def test_volume_missing_becomes_na(self, monkeypatch):
        raw = _make_raw(_DATES)
        raw = raw.drop(columns=["Volume"])
        provider = _make_provider(monkeypatch, raw)
        df = provider.fetch("SPY", _DATES[0], _DATES[-1])

        assert df["volume"].dtype == pd.Int64Dtype()
        assert pd.isna(df["volume"].iloc[0])

    def test_empty_df_returns_canonical_columns(self, monkeypatch):
        raw = pd.DataFrame()
        provider = _make_provider(monkeypatch, raw)
        df = provider.fetch("SPY", _DATES[0], _DATES[-1])

        assert df.empty
        assert list(df.columns) == _CANONICAL_COLS

    def test_output_dtypes(self, monkeypatch):
        raw = _make_raw(_DATES)
        provider = _make_provider(monkeypatch, raw)
        df = provider.fetch("SPY", _DATES[0], _DATES[-1])

        for col in ("open", "high", "low", "close", "adj_close"):
            assert df[col].dtype.kind == "f", f"{col} should be float"
        assert df["volume"].dtype == pd.Int64Dtype()
