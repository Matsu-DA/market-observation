"""Immutable parquet storage layer.

Invariants enforced here:
- 1 partition file = exactly 1 row
- observed_date is coerced to datetime.date at the API boundary
- Writes are atomic (temp file + os.replace)
- Schema is fixed by the caller (no pandas inference)
"""
from __future__ import annotations

import os
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from scripts.config import DATA_DIR


class DataIntegrityError(Exception):
    """Raised when the (source, dataset, observed_date) = 1 row invariant is broken."""


def _coerce_date(d) -> date:
    """Force any date-like input into a plain datetime.date.

    Accepts datetime.date, datetime.datetime, pandas.Timestamp, numpy.datetime64, str.
    """
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, date):
        return d
    return pd.Timestamp(d).date()


def partition_path(source: str, dataset: str, observed_date) -> Path:
    d = _coerce_date(observed_date)
    return (
        DATA_DIR
        / f"source={source}"
        / f"dataset={dataset}"
        / f"year={d.year:04d}"
        / f"month={d.month:02d}"
        / f"day={d.day:02d}"
        / "data.parquet"
    )


def exists(source: str, dataset: str, observed_date) -> bool:
    """Return True only if a healthy 1-row parquet file is already present.

    Returns False for missing files, 0-byte files, unreadable files, and
    files whose row count is not exactly 1. This prevents corrupt or
    interrupted writes from causing permanent skips.
    """
    path = partition_path(source, dataset, observed_date)
    if not path.exists() or path.stat().st_size == 0:
        return False
    try:
        meta = pq.read_metadata(path)
    except Exception:
        return False
    return meta.num_rows == 1


def write_immutable(
    df: pd.DataFrame,
    source: str,
    dataset: str,
    observed_date,
    schema: pa.Schema,
) -> bool:
    """Atomically write a single-row DataFrame to the canonical partition path.

    Returns True if a new file was written, False if it already existed.
    Raises DataIntegrityError if len(df) != 1.
    """
    if len(df) != 1:
        raise DataIntegrityError(
            f"Expected exactly 1 row for ({source}, {dataset}, {observed_date}), "
            f"got {len(df)}"
        )

    obs = _coerce_date(observed_date)
    path = partition_path(source, dataset, obs)
    if exists(source, dataset, obs):
        return False

    enriched = df.copy()
    enriched["observed_date"] = obs
    enriched["source"] = source
    enriched["dataset"] = dataset
    enriched["ingested_at"] = pd.Timestamp.now(tz="UTC").floor("us")

    enriched = enriched[[f.name for f in schema]]

    table = pa.Table.from_pandas(enriched, schema=schema, preserve_index=False)

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix=".tmp_", suffix=".parquet", dir=str(path.parent)
    )
    os.close(fd)
    try:
        pq.write_table(table, tmp_path, compression="zstd")
        os.replace(tmp_path, path)
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise
    return True
