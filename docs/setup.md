# Setup and tests

You need Python 3.10 or later, Docker Compose and a SQL client to inspect results.
Commands use Windows PowerShell and run from the project root.

## Prepare the data and configuration

Download the dataset from the
[source page](https://www.kaggle.com/datasets/mkechinov/ecommerce-events-history-in-electronics-store)
and place the archive at `data/events.csv.zip`. The ZIP must contain one CSV with
the following columns in this order.

```text
event_time,event_type,product_id,category_id,category_code,brand,price,user_id,user_session
```

An example event time is `2020-09-24 11:57:06 UTC`. The loader also accepts a
standalone CSV with the same structure.

Create `.env` in the project root and set a local password.

```dotenv
POSTGRES_HOST=localhost
POSTGRES_PORT=5433
POSTGRES_DB=electronics_events
POSTGRES_USER=electronics_user
POSTGRES_PASSWORD=replace_with_your_local_password
```

Local settings, source data and the virtual environment are excluded from Git.

## First run

Start Docker Desktop. These commands create a Python environment and start
PostgreSQL using `compose.yaml`.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
docker compose up -d
docker compose ps
```

Wait for the `healthy` status, then load the archive.

```powershell
.\.venv\Scripts\python.exe scripts/run_pipeline.py --source data/events.csv.zip
```

On the first run, the command creates the schema and views, registers a batch,
loads the source events and refreshes sessions. Running it again adds zero rows.

Generate the charts.

```powershell
.\.venv\Scripts\python.exe scripts/plot_results.py
```

Images are saved in `docs/images/`. Regenerate them after updating the data.

## Inspect database results

Connect your SQL client using the database, port, user and password from `.env`.
The main queries are below.

```sql
SELECT * FROM mart.funnel_summary;

SELECT *
FROM mart.category_conversion
ORDER BY sessions_without_cart DESC, category_code;

SELECT * FROM mart.category_data_quality;

SELECT * FROM raw.import_batches ORDER BY registered_at;
```

## Load individual batches

For a file with a stable delivery ID, use the following command.

```powershell
.\.venv\Scripts\python.exe scripts/run_pipeline.py --source data/new-events.csv --batch-id delivery-001
```

By default, the ID is derived from the content. Replay rules are described in
the [methodology](methodology.md#delivery-identity).

To use a different database, pass its configuration through `--env-file path-to-file`.

## Verify loading in parts

Use a separate empty database for the split archive. The full archive and its
parts represent the same events, so load them separately for comparison.

Create the database in your SQL client.

```sql
CREATE DATABASE electronics_batches;
```

Copy the settings and change the database in the new file to `electronics_batches`.

```powershell
Copy-Item .env data/batches.env
```

Prepare batches of 100,000 rows.

```powershell
.\.venv\Scripts\python.exe scripts/split_source.py
```

The `data/batches/` directory appears after the entire source has passed validation.
Use `--output-dir` for a different directory and `--rows-per-batch` for a different
batch size. If the directory already exists, reuse its files or choose a new path.

Load the files in order.

```powershell
Get-ChildItem data/batches/batch-*.csv | Sort-Object Name | ForEach-Object {
    .\.venv\Scripts\python.exe scripts/run_pipeline.py --source $_.FullName --env-file data/batches.env
    if ($LASTEXITCODE -ne 0) { throw "Batch load failed" }
}
```

Repeat the loop to verify redelivery.

## Adopt an earlier load

If `raw` contains data from the earlier loader, pass the complete original archive
to `run_pipeline.py`. After comparing every record in source order, the loader
registers the existing rows without changing their `raw_event_id` values.
A mismatch causes an error and a rollback.

For raw-only loading, use `scripts/load_raw.py` with the same arguments.
The next `run_pipeline.py` invocation processes pending batches.

## Tests and SQL checks

Integration tests create separate temporary databases. Configure a PostgreSQL
connection with `CREATE DATABASE` permission and your local password.

```powershell
$env:TEST_DATABASE_URL = "host=localhost port=5433 dbname=postgres user=electronics_user password=replace_with_your_local_password"
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Run each SQL check file in full against the selected database.

- `sql/03_quality_checks.sql` checks event counts and session consistency.
- `sql/05_check_marts.sql` checks funnel completeness and event order.
- `sql/07_check_category_conversion.sql` checks categories and data coverage.

Mismatch counters should be zero. These SQL files return results for inspection.
Integration tests fail when an assertion is violated.

`sql/02_build_core.sql` performs a full rebuild from `raw`. The loader applies
the `08_enable_batches.sql` migration automatically. It runs
`09_refresh_sessions.sql` within the transaction for affected sessions.

## Stop the services

```powershell
docker compose stop
```

After stopping the project services, you can close Docker Desktop from the system tray.

[Project overview](../README.md) · [Validation results](../reports/batch-processing.md)
