-- DuckDB sample queries for the market-observation lake.
-- All paths are Hive-partitioned: source=.../dataset=.../year=.../month=.../day=.../data.parquet

-- 1. DGS10 over the last year
SELECT observed_date, value
FROM read_parquet('data/source=fred/dataset=DGS10/**/*.parquet',
                  hive_partitioning = true)
WHERE observed_date >= CURRENT_DATE - INTERVAL 1 YEAR
ORDER BY observed_date;

-- 2. All FRED datasets stacked
SELECT *
FROM read_parquet('data/source=fred/dataset=*/**/*.parquet',
                  hive_partitioning = true);

-- 3. 10Y treasury joined with QQQ adjusted close (most recent 60 sessions)
SELECT f.observed_date,
       f.value     AS dgs10,
       y.adj_close AS qqq
FROM read_parquet('data/source=fred/dataset=DGS10/**/*.parquet',
                  hive_partitioning = true) f
JOIN read_parquet('data/source=yahoo/dataset=QQQ/**/*.parquet',
                  hive_partitioning = true) y USING (observed_date)
ORDER BY f.observed_date DESC
LIMIT 60;

-- 4. Yahoo monthly open/close averages for SPY
SELECT date_trunc('month', observed_date) AS month,
       avg(open)  AS open_avg,
       avg(close) AS close_avg
FROM read_parquet('data/source=yahoo/dataset=SPY/**/*.parquet',
                  hive_partitioning = true)
GROUP BY 1
ORDER BY 1 DESC
LIMIT 12;

-- 5. Ingest summary history (success/error counts)
SELECT run_at, kind, written, skipped_exists, skipped_empty,
       json_array_length(errors) AS error_count
FROM read_json_auto('logs/summary_*.json');

-- 6. Schema inspection
DESCRIBE
SELECT *
FROM read_parquet('data/source=fred/dataset=DGS10/**/*.parquet',
                  hive_partitioning = true);
