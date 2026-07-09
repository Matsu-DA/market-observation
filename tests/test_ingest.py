"""Tests for scripts.ingest.run_ingest."""
from __future__ import annotations

import datetime
import json
from pathlib import Path

import pandas as pd
import pytest

from scripts import ingest, storage
from scripts.providers.base import MarketDataProvider
from scripts.providers.schema import FRED_SCHEMA


# ---------------------------------------------------------------------------
# Stub provider
# ---------------------------------------------------------------------------

class _StubProvider(MarketDataProvider):
    """Returns a fixed DataFrame (or raises) for every fetch call."""

    def __init__(self, name: str, response):
        self.name = name
        self._response = response

    def fetch(self, dataset: str, start: datetime.date, end: datetime.date) -> pd.DataFrame:
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


def _fred_df(d: datetime.date, value: float = 1.0) -> pd.DataFrame:
    return pd.DataFrame({"observed_date": [d], "value": [value]})


def _yahoo_df(d: datetime.date) -> pd.DataFrame:
    return pd.DataFrame({
        "observed_date": [d],
        "open": [100.0], "high": [105.0], "low": [99.0],
        "close": [103.0], "adj_close": [103.0], "volume": pd.array([1_000_000], dtype="Int64"),
    })


FIXED_DATE = datetime.date(2026, 1, 5)


def _setup(monkeypatch, tmp_path: Path, fred_resp, yahoo_resp=None, datasets=("DS1",)):
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    monkeypatch.setattr(ingest, "LOGS_DIR", tmp_path / "logs")
    monkeypatch.setattr(ingest, "FRED_DATASETS", list(datasets))
    monkeypatch.setattr(ingest, "YAHOO_DATASETS", [])

    fred_stub = _StubProvider("fred", fred_resp)
    yahoo_stub = _StubProvider("yahoo", yahoo_resp or pd.DataFrame(
        columns=["observed_date", "open", "high", "low", "close", "adj_close", "volume"]
    ))
    monkeypatch.setattr(ingest, "_build_providers", lambda: {"fred": fred_stub, "yahoo": yahoo_stub})


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestRunIngest:
    def test_successful_fetch_writes_parquet(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path, _fred_df(FIXED_DATE))
        result = ingest.run_ingest("daily", FIXED_DATE, 0)

        assert result.written == 1
        assert result.errors == []
        path = storage.partition_path("fred", "DS1", FIXED_DATE)
        assert path.exists()

    def test_summary_json_written_with_stale_datasets_key(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path, _fred_df(FIXED_DATE))
        ingest.run_ingest("daily", FIXED_DATE, 0)

        logs_dir = tmp_path / "logs"
        summaries = list(logs_dir.glob("summary_*.json"))
        assert len(summaries) == 1
        data = json.loads(summaries[0].read_text())
        assert "stale_datasets" in data

    def test_existing_partition_skipped(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path, _fred_df(FIXED_DATE))
        # Pre-write the partition
        storage.write_immutable(
            _fred_df(FIXED_DATE), "fred", "DS1", FIXED_DATE, FRED_SCHEMA,
        )
        result = ingest.run_ingest("daily", FIXED_DATE, 0)

        assert result.skipped_exists >= 1
        assert result.written == 0

    def test_empty_fetch_counted_as_skipped_empty(self, monkeypatch, tmp_path):
        empty_df = pd.DataFrame({"observed_date": [], "value": []})
        _setup(monkeypatch, tmp_path, empty_df)
        result = ingest.run_ingest("daily", FIXED_DATE, 0)

        assert result.skipped_empty >= 1
        assert result.written == 0

    def test_fetch_exception_recorded_in_errors(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path, RuntimeError("API down"))
        result = ingest.run_ingest("daily", FIXED_DATE, 0)

        assert len(result.errors) == 1
        err = result.errors[0]
        assert err["source"] == "fred"
        assert err["dataset"] == "DS1"
        assert "error" in err

    def test_stale_dataset_not_listed_when_recent_write(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path, _fred_df(FIXED_DATE))
        result = ingest.run_ingest("daily", FIXED_DATE, 0)

        # Dataset was just written so it should NOT appear in stale_datasets
        assert not any(
            s["dataset"] == "DS1" for s in result.stale_datasets
        )

    def test_stale_dataset_listed_when_no_partitions(self, monkeypatch, tmp_path):
        # Always return empty df so nothing is ever written
        empty_df = pd.DataFrame({"observed_date": [], "value": []})
        _setup(monkeypatch, tmp_path, empty_df)
        result = ingest.run_ingest("daily", FIXED_DATE, 0)

        assert any(s["dataset"] == "DS1" for s in result.stale_datasets)

    def test_error_ratio_with_one_error_of_one_dataset(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path, RuntimeError("fail"))
        result = ingest.run_ingest("daily", FIXED_DATE, 0)

        assert result.total_datasets == 1
        assert result.error_ratio() == pytest.approx(1.0)

    def test_error_ratio_zero_datasets(self, monkeypatch, tmp_path):
        monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
        monkeypatch.setattr(ingest, "LOGS_DIR", tmp_path / "logs")
        monkeypatch.setattr(ingest, "FRED_DATASETS", [])
        monkeypatch.setattr(ingest, "YAHOO_DATASETS", [])
        monkeypatch.setattr(ingest, "_build_providers", lambda: {"fred": _StubProvider("fred", pd.DataFrame()), "yahoo": _StubProvider("yahoo", pd.DataFrame())})

        result = ingest.run_ingest("daily", FIXED_DATE, 0)
        assert result.error_ratio() == 0.0

    def test_error_ratio_partial(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path, _fred_df(FIXED_DATE), datasets=("DS1", "DS2", "DS3"))

        call_count = [0]
        orig_ingest_dataset = ingest._ingest_dataset

        def _patched_ingest(provider, dataset, target_days, result):
            call_count[0] += 1
            if dataset == "DS1":
                result.errors.append({"source": "fred", "dataset": "DS1", "error": "fail"})
            else:
                orig_ingest_dataset(provider, dataset, target_days, result)

        monkeypatch.setattr(ingest, "_ingest_dataset", _patched_ingest)
        result = ingest.run_ingest("daily", FIXED_DATE, 0)

        assert result.total_datasets == 3
        assert result.error_ratio() == pytest.approx(1 / 3)
