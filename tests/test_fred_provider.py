"""Tests for scripts.providers.fred.FredProvider."""
from __future__ import annotations

import datetime
import xml.etree.ElementTree as ET
from urllib.error import HTTPError

import pandas as pd
import pytest
import requests

from scripts.config import RETRY_ATTEMPTS
from scripts.providers.fred import FredApiError, FredProvider, _find_http_error


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_provider(monkeypatch) -> FredProvider:
    # Patch requests.get so _diagnose never touches the network
    monkeypatch.setattr(
        requests, "get",
        lambda *a, **k: _FakeResp(200, "OK"),
    )
    monkeypatch.setattr("tenacity.nap.time.sleep", lambda s: None)
    return FredProvider(api_key="test")


class _FakeResp:
    def __init__(self, status_code: int, reason: str = ""):
        self.status_code = status_code
        self.reason = reason


def _fredapi_style_error(code: int | None, message):
    """Return a callable that mimics fredapi's error-raising pattern.

    fredapi raises ValueError after catching an HTTPError, so the HTTPError is
    preserved as __context__ on the ValueError.
    """
    def _raiser(*a, **k):
        if code is not None:
            try:
                raise HTTPError("http://x", code, "err", None, None)
            except HTTPError:
                raise ValueError(message)
        else:
            raise ValueError(message)
    return _raiser


def _fredapi_style_parse_error_with_http(code: int):
    """ParseError raised inside except-HTTPError, so HTTPError is __context__."""
    def _raiser(*a, **k):
        try:
            raise HTTPError("http://x", code, "err", None, None)
        except HTTPError:
            raise ET.ParseError("bad xml")
    return _raiser


def _counter_wrapper(fn):
    """Wrap a callable and count how many times it was called."""
    calls = []

    def _wrapper(*a, **k):
        calls.append(1)
        return fn(*a, **k)

    _wrapper.calls = calls
    return _wrapper


# ---------------------------------------------------------------------------
# Normal fetch
# ---------------------------------------------------------------------------

class TestFetchNormal:
    def test_series_to_canonical_df(self, monkeypatch):
        provider = _make_provider(monkeypatch)
        idx = pd.to_datetime(["2026-01-05", "2026-01-06"])
        series = pd.Series([1.5, 2.0], index=idx, name="DGS10")
        monkeypatch.setattr(provider._fred, "get_series", lambda *a, **k: series)

        df = provider.fetch("DGS10", datetime.date(2026, 1, 5), datetime.date(2026, 1, 6))

        assert list(df.columns) == ["observed_date", "value"]
        assert df["observed_date"].iloc[0] == datetime.date(2026, 1, 5)
        assert isinstance(df["observed_date"].iloc[0], datetime.date)
        assert df["value"].dtype.kind == "f"
        assert len(df) == 2

    def test_empty_series_returns_empty_df(self, monkeypatch):
        provider = _make_provider(monkeypatch)
        monkeypatch.setattr(
            provider._fred, "get_series",
            lambda *a, **k: pd.Series([], dtype=float),
        )
        df = provider.fetch("DGS10", datetime.date(2026, 1, 5), datetime.date(2026, 1, 5))
        assert df.empty
        assert list(df.columns) == ["observed_date", "value"]


# ---------------------------------------------------------------------------
# Retry behaviour
# ---------------------------------------------------------------------------

class TestRetryBehaviour:
    def test_504_retries_and_raises_fred_api_error(self, monkeypatch):
        provider = _make_provider(monkeypatch)
        fn = _counter_wrapper(_fredapi_style_error(504, None))
        monkeypatch.setattr(provider._fred, "get_series", fn)

        with pytest.raises(FredApiError, match="unreachable"):
            provider.fetch("DGS10", datetime.date(2026, 1, 5), datetime.date(2026, 1, 5))

        assert len(fn.calls) == RETRY_ATTEMPTS

    def test_404_no_retry(self, monkeypatch):
        provider = _make_provider(monkeypatch)
        fn = _counter_wrapper(_fredapi_style_error(404, "Not Found"))
        monkeypatch.setattr(provider._fred, "get_series", fn)

        with pytest.raises(FredApiError):
            provider.fetch("DGS10", datetime.date(2026, 1, 5), datetime.date(2026, 1, 5))

        assert len(fn.calls) == 1

    def test_400_with_chain_no_retry(self, monkeypatch):
        provider = _make_provider(monkeypatch)
        fn = _counter_wrapper(_fredapi_style_error(400, "Bad Request"))
        monkeypatch.setattr(provider._fred, "get_series", fn)

        with pytest.raises(FredApiError, match="Bad Request"):
            provider.fetch("DGS10", datetime.date(2026, 1, 5), datetime.date(2026, 1, 5))

        assert len(fn.calls) == 1

    def test_descriptive_value_error_no_chain_no_retry(self, monkeypatch):
        provider = _make_provider(monkeypatch)

        def _raiser(*a, **k):
            raise ValueError("The series does not exist.")

        fn = _counter_wrapper(_raiser)
        monkeypatch.setattr(provider._fred, "get_series", fn)

        with pytest.raises(FredApiError, match="The series does not exist"):
            provider.fetch("DGS10", datetime.date(2026, 1, 5), datetime.date(2026, 1, 5))

        assert len(fn.calls) == 1

    def test_parse_error_alone_no_retry(self, monkeypatch):
        provider = _make_provider(monkeypatch)

        def _raiser(*a, **k):
            raise ET.ParseError("syntax error")

        fn = _counter_wrapper(_raiser)
        monkeypatch.setattr(provider._fred, "get_series", fn)

        with pytest.raises(FredApiError):
            provider.fetch("DGS10", datetime.date(2026, 1, 5), datetime.date(2026, 1, 5))

        assert len(fn.calls) == 1

    def test_parse_error_with_503_chain_retries(self, monkeypatch):
        provider = _make_provider(monkeypatch)
        fn = _counter_wrapper(_fredapi_style_parse_error_with_http(503))
        monkeypatch.setattr(provider._fred, "get_series", fn)

        with pytest.raises(FredApiError):
            provider.fetch("DGS10", datetime.date(2026, 1, 5), datetime.date(2026, 1, 5))

        assert len(fn.calls) == RETRY_ATTEMPTS

    def test_value_error_none_from_none_treated_as_transient(self, monkeypatch):
        """raise ValueError('None') from None severs context; treated as transient."""
        provider = _make_provider(monkeypatch)

        def _raiser(*a, **k):
            try:
                raise HTTPError("http://x", 504, "err", None, None)
            except HTTPError:
                raise ValueError("None") from None

        fn = _counter_wrapper(_raiser)
        monkeypatch.setattr(provider._fred, "get_series", fn)

        with pytest.raises(FredApiError, match="unreachable"):
            provider.fetch("DGS10", datetime.date(2026, 1, 5), datetime.date(2026, 1, 5))

        assert len(fn.calls) == RETRY_ATTEMPTS

    def test_connection_error_retries(self, monkeypatch):
        provider = _make_provider(monkeypatch)

        def _raiser(*a, **k):
            raise ConnectionError("network down")

        fn = _counter_wrapper(_raiser)
        monkeypatch.setattr(provider._fred, "get_series", fn)

        with pytest.raises(ConnectionError):
            provider.fetch("DGS10", datetime.date(2026, 1, 5), datetime.date(2026, 1, 5))

        assert len(fn.calls) == RETRY_ATTEMPTS


# ---------------------------------------------------------------------------
# _find_http_error unit tests
# ---------------------------------------------------------------------------

class TestFindHttpError:
    def test_direct_http_error(self):
        exc = HTTPError("http://x", 503, "Service Unavailable", None, None)
        assert _find_http_error(exc) is exc

    def test_cause_chain(self):
        http = HTTPError("http://x", 500, "Internal Server Error", None, None)
        ve = ValueError("oops")
        ve.__cause__ = http
        assert _find_http_error(ve) is http

    def test_context_chain(self):
        # __context__ is set only when an exception is *raised* inside an except block
        http = HTTPError("http://x", 502, "Bad Gateway", None, None)
        try:
            try:
                raise http
            except HTTPError:
                raise ValueError("wrapped")
        except ValueError as ve:
            captured = ve
        assert _find_http_error(captured) is http

    def test_from_none_returns_none(self):
        try:
            raise HTTPError("http://x", 504, "Gateway Timeout", None, None)
        except HTTPError:
            ve = ValueError("severed")
            ve.__suppress_context__ = True
            ve.__context__ = None
        assert _find_http_error(ve) is None

    def test_no_chain_returns_none(self):
        ve = ValueError("standalone")
        assert _find_http_error(ve) is None
