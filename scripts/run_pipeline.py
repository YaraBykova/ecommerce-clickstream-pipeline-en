"""Batch loading and session refresh. Draft prepared for joint review."""

import argparse
from pathlib import Path

from load_raw import (
    ARCHIVE_PATH,
    LOCK_KEY,
    connect_database,
    ensure_batch_schema,
    execute_script,
    load_batch,
)


def initialize_database(connection):
    """Create the schema and views on first use of the selected database."""
    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK_KEY,))
        cursor.execute("SELECT to_regclass('raw.events_import')")
        if cursor.fetchone()[0] is None:
            execute_script(cursor, "01_schema.sql")
        ensure_batch_schema(cursor)
        cursor.execute("""
            SELECT to_regclass('mart.session_funnel'), to_regclass('mart.funnel_summary')
        """)
        if any(value is None for value in cursor.fetchone()):
            execute_script(cursor, "04_build_marts.sql")
        cursor.execute("""
            SELECT to_regclass('mart.session_category_funnel'),
                to_regclass('mart.category_conversion'), to_regclass('mart.category_data_quality')
        """)
        missing_category_views = any(value is None for value in cursor.fetchone())
        cursor.execute("""
            SELECT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'mart' AND table_name = 'category_conversion'
                  AND column_name = 'category_id'
            )
        """)
        legacy_categories = cursor.fetchone()[0]
        if missing_category_views or legacy_categories:
            execute_script(cursor, "06_category_conversion.sql")


def run_pipeline(connection, source_path=None, batch_id=None):
    initialize_database(connection)
    return load_batch(connection, source_path, batch_id, update_core=True)


def main():
    parser = argparse.ArgumentParser(description="Load a batch and refresh affected sessions")
    parser.add_argument("--source", type=Path, default=ARCHIVE_PATH)
    parser.add_argument("--batch-id")
    parser.add_argument("--env-file", type=Path)
    args = parser.parse_args()
    with connect_database(args.env_file) as connection:
        result = run_pipeline(connection, args.source, args.batch_id)
    print(f"Batch {result.batch_id}: {result.status}")
    print(
        f"Source rows: {result.source_rows}; inserted: {result.inserted_rows}; "
        f"adopted: {result.adopted_rows}; "
        f"refreshed sessions: {result.affected_sessions}"
    )


if __name__ == "__main__":
    main()
