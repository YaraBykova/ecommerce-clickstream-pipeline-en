"""CSV/ZIP batch loading. Prepared by an agent for joint review."""

import argparse
import csv
import hashlib
import io
import json
import zipfile
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

import psycopg


PROJECT_DIR = Path(__file__).resolve().parent.parent
ARCHIVE_PATH = PROJECT_DIR / "data" / "events.csv.zip"
ENV_PATH = PROJECT_DIR / ".env"
LOCK_KEY = 7301926

EXPECTED_COLUMNS = (
    "event_time",
    "event_type",
    "product_id",
    "category_id",
    "category_code",
    "brand",
    "price",
    "user_id",
    "user_session",
)

REQUIRED_COLUMNS = (
    "event_time",
    "event_type",
    "product_id",
    "category_id",
    "price",
    "user_id",
)

COPY_SQL = """
    COPY raw.events_import (
        source_file_name,
        event_time,
        event_type,
        product_id,
        category_id,
        category_code,
        brand,
        price,
        user_id,
        user_session
    ) FROM STDIN
"""


@dataclass(frozen=True)
class BatchResult:
    batch_id: str
    status: str
    source_rows: int
    inserted_rows: int = 0
    adopted_rows: int = 0
    affected_sessions: int = 0


def read_settings(env_path=None):
    """Read connection settings from a local .env file."""
    settings_path = Path(env_path) if env_path is not None else ENV_PATH
    if not settings_path.is_file():
        raise FileNotFoundError(f"Settings file not found: {settings_path}")

    settings = {}
    for line_number, line in enumerate(
        settings_path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"Invalid line {line_number} in {settings_path.name}")

        key, value = line.split("=", 1)
        settings[key.strip()] = value.strip()

    required_settings = (
        "POSTGRES_DB",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "POSTGRES_PORT",
    )
    missing_settings = [
        name for name in required_settings if not settings.get(name)
    ]
    if missing_settings:
        raise ValueError("Missing .env settings: " + ", ".join(missing_settings))

    return settings


def connect_database(env_path=None):
    settings = read_settings(env_path)
    return psycopg.connect(
        host=settings.get("POSTGRES_HOST", "localhost"),
        port=int(settings["POSTGRES_PORT"]),
        dbname=settings["POSTGRES_DB"],
        user=settings["POSTGRES_USER"],
        password=settings["POSTGRES_PASSWORD"],
        autocommit=True,
    )


def execute_script(cursor, name):
    """Execute project SQL within the caller's transaction."""
    lines = (PROJECT_DIR / "sql" / name).read_text(encoding="utf-8").splitlines()
    script = "\n".join(line for line in lines if line.strip() not in {"BEGIN;", "COMMIT;"})
    cursor.execute(script)


def ensure_batch_schema(cursor):
    cursor.execute("SELECT to_regclass('raw.events_import_batch_row_key')")
    if cursor.fetchone()[0] is None:
        execute_script(cursor, "08_enable_batches.sql")


def empty_to_none(value):
    """Convert an empty optional field to SQL NULL."""
    value = value.strip()
    return value if value else None


def prepare_row(row, source_file_name, line_number):
    """Validate one CSV row and prepare it for PostgreSQL."""
    if None in row or any(row.get(column) is None for column in EXPECTED_COLUMNS):
        raise ValueError(f"CSV row {line_number}: incorrect field count")
    for column in REQUIRED_COLUMNS:
        if not row[column].strip():
            raise ValueError(
                f"CSV row {line_number}: empty required field {column!r}"
            )

    try:
        event_time = datetime.strptime(
            row["event_time"].strip(), "%Y-%m-%d %H:%M:%S UTC"
        ).replace(tzinfo=timezone.utc)
        product_id = int(row["product_id"].strip())
        category_id = int(row["category_id"].strip())
        price = Decimal(row["price"].strip())
        user_id = int(row["user_id"].strip())
    except (ValueError, InvalidOperation) as error:
        raise ValueError(
            f"CSV row {line_number}: could not convert data types"
        ) from error

    if not price.is_finite() or price < 0:
        raise ValueError(f"CSV row {line_number}: price must be finite and nonnegative")
    if price.as_tuple().exponent < -2:
        raise ValueError(
            f"CSV row {line_number}: price has more than two decimal places"
        )

    event_type = row["event_type"].strip()
    if event_type not in {"view", "cart", "purchase"}:
        raise ValueError(f"CSV row {line_number}: unknown event_type {event_type!r}")

    return (
        source_file_name,
        event_time,
        event_type,
        product_id,
        category_id,
        empty_to_none(row["category_code"]),
        empty_to_none(row["brand"]),
        price,
        user_id,
        empty_to_none(row["user_session"]),
    )


def iter_source_rows(source_path=None):
    """Stream CSV records without loading the entire source into memory."""
    source_path = Path(source_path) if source_path is not None else ARCHIVE_PATH
    if not source_path.is_file():
        raise FileNotFoundError(f"Source not found: {source_path}")
    with ExitStack() as stack:
        if source_path.suffix.lower() == ".zip":
            archive = stack.enter_context(zipfile.ZipFile(source_path))
            csv_files = [item for item in archive.infolist() if item.filename.lower().endswith(".csv")]
            if len(csv_files) != 1:
                raise ValueError(f"ZIP must contain exactly one CSV file. Found: {len(csv_files)}")
            binary = stack.enter_context(archive.open(csv_files[0]))
            text_file = stack.enter_context(io.TextIOWrapper(binary, encoding="utf-8-sig", newline=""))
        elif source_path.suffix.lower() == ".csv":
            text_file = stack.enter_context(source_path.open(encoding="utf-8-sig", newline=""))
        else:
            raise ValueError("Source must have a .csv or .zip extension")
        reader = csv.DictReader(text_file)
        if tuple(reader.fieldnames or ()) != EXPECTED_COLUMNS:
            raise ValueError("CSV structure does not match the expected columns")
        for line_number, row in enumerate(reader, start=2):
            yield prepare_row(row, source_path.name, line_number)


def adopt_legacy_rows(cursor, batch_id):
    """Adopt an earlier load after comparing every source record."""
    cursor.execute("SELECT COUNT(*) FROM raw.events_import WHERE batch_id IS NULL")
    legacy_count = cursor.fetchone()[0]
    if not legacy_count:
        return 0
    cursor.execute("DROP TABLE IF EXISTS pg_temp.batch_legacy_events")
    cursor.execute("""
        CREATE TEMP TABLE batch_legacy_events ON COMMIT DROP AS
        SELECT ROW_NUMBER() OVER (ORDER BY raw_event_id) AS legacy_row, event.*
        FROM raw.events_import AS event WHERE batch_id IS NULL
    """)
    cursor.execute("""
        SELECT EXISTS (
            SELECT 1 FROM batch_legacy_events AS legacy
            FULL JOIN batch_incoming_events AS incoming ON incoming.source_row = legacy.legacy_row
            WHERE legacy.raw_event_id IS NULL OR incoming.source_row IS NULL
               OR ROW(legacy.event_time, legacy.event_type, legacy.product_id,
                      legacy.category_id, legacy.category_code, legacy.brand,
                      legacy.price, legacy.user_id, legacy.user_session)
                  IS DISTINCT FROM
                  ROW(incoming.event_time, incoming.event_type, incoming.product_id,
                      incoming.category_id, incoming.category_code, incoming.brand,
                      incoming.price, incoming.user_id, incoming.user_session)
        )
    """)
    if cursor.fetchone()[0]:
        raise ValueError(
            "Raw contains an earlier load. First supply the complete original "
            "archive. The loader will compare and register its rows without reinserting them."
        )
    cursor.execute("""
        UPDATE raw.events_import AS event SET batch_id = %s, source_row = legacy.legacy_row
        FROM batch_legacy_events AS legacy WHERE event.raw_event_id = legacy.raw_event_id
    """, (batch_id,))
    return legacy_count


def refresh_pending_sessions(cursor):
    """Apply all registered pending batches to core."""
    cursor.execute("DROP TABLE IF EXISTS pg_temp.batch_affected_sessions")
    cursor.execute("""
        CREATE TEMP TABLE batch_affected_sessions ON COMMIT DROP AS
        SELECT DISTINCT event.user_session AS session_id
        FROM raw.events_import AS event
        JOIN raw.import_batches AS batch USING (batch_id)
        WHERE batch.core_applied_at IS NULL
          AND event.user_session IS NOT NULL AND BTRIM(event.user_session) <> ''
    """)
    cursor.execute("SELECT COUNT(*) FROM batch_affected_sessions")
    affected_count = cursor.fetchone()[0]
    if affected_count:
        execute_script(cursor, "09_refresh_sessions.sql")
    cursor.execute("""
        UPDATE raw.import_batches SET core_applied_at = CURRENT_TIMESTAMP
        WHERE core_applied_at IS NULL
    """)
    return affected_count


def load_batch(connection, source_path=None, batch_id=None, update_core=False):
    """Register a batch and atomically apply it to raw and, optionally, core."""
    source_path = Path(source_path) if source_path is not None else ARCHIVE_PATH
    if batch_id is not None and not batch_id.strip():
        raise ValueError("batch_id must be nonempty")
    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK_KEY,))
        ensure_batch_schema(cursor)
        cursor.execute("DROP TABLE IF EXISTS pg_temp.batch_incoming_events")
        cursor.execute("""
            CREATE TEMP TABLE batch_incoming_events ON COMMIT DROP AS
            SELECT source_row, source_file_name, event_time, event_type, product_id,
                category_id, category_code, brand, price, user_id, user_session
            FROM raw.events_import WITH NO DATA
        """)
        stage_copy = COPY_SQL.replace("raw.events_import (", "batch_incoming_events (source_row,")
        digest = hashlib.sha256()
        source_rows = 0
        with cursor.copy(stage_copy) as copy:
            for source_rows, row in enumerate(iter_source_rows(source_path), start=1):
                copy.write_row((source_rows, *row))
                (
                    _, event_time, event_type, product_id, category_id,
                    category_code, brand, price, user_id, user_session,
                ) = row
                canonical = (
                    event_time.isoformat(), event_type, product_id, category_id,
                    category_code, brand, format(price, ".2f"), user_id, user_session,
                )
                encoded_row = json.dumps(canonical, ensure_ascii=False, separators=(",", ":"))
                digest.update(encoded_row.encode("utf-8"))
                digest.update(b"\n")
        content_sha256 = digest.hexdigest()
        batch_id = batch_id if batch_id is not None else "sha256:" + content_sha256
        cursor.execute(
            "SELECT content_sha256, source_rows FROM raw.import_batches WHERE batch_id = %s",
            (batch_id,),
        )
        previous = cursor.fetchone()
        inserted_rows = 0
        adopted_rows = 0
        status = "replayed"
        if previous is not None:
            if previous != (content_sha256, source_rows):
                raise ValueError(f"Content of batch {batch_id!r} changed under the same ID")
        else:
            cursor.execute("""
                INSERT INTO raw.import_batches (batch_id, content_sha256, source_file_name, source_rows)
                VALUES (%s, %s, %s, %s)
            """, (batch_id, content_sha256, source_path.name, source_rows))
            adopted_rows = adopt_legacy_rows(cursor, batch_id)
            status = "adopted" if adopted_rows else "loaded"
            if not adopted_rows:
                cursor.execute("""
                    INSERT INTO raw.events_import (
                        batch_id, source_row, source_file_name, event_time, event_type,
                        product_id, category_id, category_code, brand, price, user_id, user_session
                    )
                    SELECT %s, source_row, source_file_name, event_time, event_type,
                        product_id, category_id, category_code, brand, price, user_id, user_session
                    FROM batch_incoming_events ORDER BY source_row
                """, (batch_id,))
                inserted_rows = cursor.rowcount
        affected_sessions = refresh_pending_sessions(cursor) if update_core else 0
        return BatchResult(
            batch_id=batch_id,
            status=status,
            source_rows=source_rows,
            inserted_rows=inserted_rows,
            adopted_rows=adopted_rows,
            affected_sessions=affected_sessions,
        )


def load_raw(source_path=None, batch_id=None, env_path=None):
    """Load a batch into raw and return the inserted row count."""
    with connect_database(env_path) as connection:
        return load_batch(connection, source_path, batch_id).inserted_rows


def main():
    parser = argparse.ArgumentParser(description="Load CSV/ZIP into raw with replay protection")
    parser.add_argument("--source", type=Path, default=ARCHIVE_PATH)
    parser.add_argument("--batch-id")
    parser.add_argument("--env-file", type=Path)
    args = parser.parse_args()
    loaded_rows = load_raw(args.source, args.batch_id, args.env_file)
    print(f"Done: loaded {loaded_rows:,} rows into raw.events_import")


if __name__ == "__main__":
    main()
