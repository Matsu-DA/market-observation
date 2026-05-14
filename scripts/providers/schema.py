import pyarrow as pa

FRED_SCHEMA = pa.schema(
    [
        pa.field("observed_date", pa.date32(), nullable=False),
        pa.field("source", pa.string(), nullable=False),
        pa.field("dataset", pa.string(), nullable=False),
        pa.field("value", pa.float64(), nullable=True),
        pa.field("ingested_at", pa.timestamp("us", tz="UTC"), nullable=False),
    ]
)

YAHOO_SCHEMA = pa.schema(
    [
        pa.field("observed_date", pa.date32(), nullable=False),
        pa.field("source", pa.string(), nullable=False),
        pa.field("dataset", pa.string(), nullable=False),
        pa.field("open", pa.float64(), nullable=True),
        pa.field("high", pa.float64(), nullable=True),
        pa.field("low", pa.float64(), nullable=True),
        pa.field("close", pa.float64(), nullable=True),
        pa.field("adj_close", pa.float64(), nullable=True),
        pa.field("volume", pa.int64(), nullable=True),
        pa.field("ingested_at", pa.timestamp("us", tz="UTC"), nullable=False),
    ]
)

SCHEMAS = {
    "fred": FRED_SCHEMA,
    "yahoo": YAHOO_SCHEMA,
}
