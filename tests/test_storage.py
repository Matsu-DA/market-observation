"""Tests for scripts.storage."""
from __future__ import annotations

import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from scripts import storage
from scripts.providers.schema import FRED_SCHEMA


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _one_row_df() -> pd.DataFrame:
    return pd.DataFrame({"value": [1.23]})


def _write_one_row(tmp_path: Path, source: str, dataset: str, d: datetime.date) -> bool:
    return storage.write_immutable(_one_row_df(), source, dataset, d, FRED_SCHEMA)


# ---------------------------------------------------------------------------
# _coerce_date
# ---------------------------------------------------------------------------

class TestCoerceDate:
    REF = datetime.date(2026, 1, 5)

    def test_date(self):
        assert storage._coerce_date(datetime.date(2026, 1, 5)) == self.REF

    def test_datetime(self):
        assert storage._coerce_date(datetime.datetime(2026, 1, 5, 12, 0)) == self.REF

    def test_timestamp(self):
        assert storage._coerce_date(pd.Timestamp("2026-01-05")) == self.REF

    def test_string(self):
        assert storage._coerce_date("2026-01-05") == self.REF

    def test_np_datetime64(self):
        assert storage._coerce_date(np.datetime64("2026-01-05")) == self.REF


# ---------------------------------------------------------------------------
# partition_path
# ---------------------------------------------------------------------------

class TestPartitionPath:
    def test_zero_padded(self, monkeypatch, tmp_path):
        monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
        d = datetime.date(2026, 1, 5)
        p = storage.partition_path("fred", "DGS10", d)
        assert str(p).endswith(
            "source=fred/dataset=DGS10/year=2026/month=01/day=05/data.parquet"
        )


# ---------------------------------------------------------------------------
# write_immutable
# ---------------------------------------------------------------------------

class TestWriteImmutable:
    def test_write_returns_true_and_creates_file(self, monkeypatch, tmp_path):
        monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
        d = datetime.date(2026, 1, 5)
        result = _write_one_row(tmp_path, "fred", "DGS10", d)
        assert result is True
        path = storage.partition_path("fred", "DGS10", d)
        assert path.exists()

    def test_written_file_has_one_row(self, monkeypatch, tmp_path):
        monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
        d = datetime.date(2026, 1, 5)
        _write_one_row(tmp_path, "fred", "DGS10", d)
        path = storage.partition_path("fred", "DGS10", d)
        meta = pq.read_metadata(path)
        assert meta.num_rows == 1

    def test_written_file_has_schema_column_order(self, monkeypatch, tmp_path):
        monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
        d = datetime.date(2026, 1, 5)
        _write_one_row(tmp_path, "fred", "DGS10", d)
        path = storage.partition_path("fred", "DGS10", d)
        # Use ParquetFile to read a single file directly, bypassing hive-partition detection
        table = pq.ParquetFile(path).read()
        assert list(table.schema.names) == [f.name for f in FRED_SCHEMA]

    def test_enrich_columns_present(self, monkeypatch, tmp_path):
        monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
        d = datetime.date(2026, 1, 5)
        _write_one_row(tmp_path, "fred", "DGS10", d)
        path = storage.partition_path("fred", "DGS10", d)
        table = pq.ParquetFile(path).read()
        df = table.to_pandas()
        assert df["observed_date"].iloc[0] == d
        assert df["source"].iloc[0] == "fred"
        assert df["dataset"].iloc[0] == "DGS10"
        assert pd.notna(df["ingested_at"].iloc[0])

    def test_second_write_returns_false_and_file_unchanged(self, monkeypatch, tmp_path):
        monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
        d = datetime.date(2026, 1, 5)
        _write_one_row(tmp_path, "fred", "DGS10", d)
        path = storage.partition_path("fred", "DGS10", d)
        mtime_before = path.stat().st_mtime

        result = storage.write_immutable(
            pd.DataFrame({"value": [99.99]}), "fred", "DGS10", d, FRED_SCHEMA
        )
        assert result is False
        assert path.stat().st_mtime == mtime_before

    def test_zero_rows_raises(self, monkeypatch, tmp_path):
        monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
        with pytest.raises(storage.DataIntegrityError):
            storage.write_immutable(
                pd.DataFrame({"value": []}), "fred", "DGS10",
                datetime.date(2026, 1, 5), FRED_SCHEMA
            )

    def test_two_rows_raises(self, monkeypatch, tmp_path):
        monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
        with pytest.raises(storage.DataIntegrityError):
            storage.write_immutable(
                pd.DataFrame({"value": [1.0, 2.0]}), "fred", "DGS10",
                datetime.date(2026, 1, 5), FRED_SCHEMA
            )

    def test_atomic_write_failure_leaves_no_parquet(self, monkeypatch, tmp_path):
        monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
        d = datetime.date(2026, 1, 5)

        def _boom(*args, **kwargs):
            raise OSError("simulated write failure")

        monkeypatch.setattr(storage.pq, "write_table", _boom)
        with pytest.raises(OSError):
            _write_one_row(tmp_path, "fred", "DGS10", d)

        path = storage.partition_path("fred", "DGS10", d)
        assert not path.exists()

    def test_atomic_write_failure_leaves_no_tmp_files(self, monkeypatch, tmp_path):
        monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
        d = datetime.date(2026, 1, 5)

        def _boom(*args, **kwargs):
            raise OSError("simulated write failure")

        monkeypatch.setattr(storage.pq, "write_table", _boom)
        with pytest.raises(OSError):
            _write_one_row(tmp_path, "fred", "DGS10", d)

        partition_dir = storage.partition_path("fred", "DGS10", d).parent
        tmp_files = list(partition_dir.glob(".tmp_*")) if partition_dir.exists() else []
        assert tmp_files == []


# ---------------------------------------------------------------------------
# exists
# ---------------------------------------------------------------------------

class TestExists:
    def test_missing_file_returns_false(self, monkeypatch, tmp_path):
        monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
        assert storage.exists("fred", "DGS10", datetime.date(2026, 1, 5)) is False

    def test_zero_byte_file_returns_false(self, monkeypatch, tmp_path):
        monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
        d = datetime.date(2026, 1, 5)
        path = storage.partition_path("fred", "DGS10", d)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"")
        assert storage.exists("fred", "DGS10", d) is False

    def test_corrupt_parquet_returns_false(self, monkeypatch, tmp_path):
        monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
        d = datetime.date(2026, 1, 5)
        path = storage.partition_path("fred", "DGS10", d)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"not a parquet file at all")
        assert storage.exists("fred", "DGS10", d) is False

    def test_healthy_one_row_returns_true(self, monkeypatch, tmp_path):
        monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
        d = datetime.date(2026, 1, 5)
        _write_one_row(tmp_path, "fred", "DGS10", d)
        assert storage.exists("fred", "DGS10", d) is True

    def test_two_row_parquet_returns_false(self, monkeypatch, tmp_path):
        monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
        d = datetime.date(2026, 1, 5)
        path = storage.partition_path("fred", "DGS10", d)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write a 2-row parquet directly (bypassing write_immutable)
        table = pa.table(
            {
                "observed_date": pa.array([d, d], type=pa.date32()),
                "source": pa.array(["fred", "fred"]),
                "dataset": pa.array(["DGS10", "DGS10"]),
                "value": pa.array([1.0, 2.0], type=pa.float64()),
                "ingested_at": pa.array(
                    [pd.Timestamp.now(tz="UTC").floor("us")] * 2,
                    type=pa.timestamp("us", tz="UTC"),
                ),
            }
        )
        pq.write_table(table, path)
        assert storage.exists("fred", "DGS10", d) is False
