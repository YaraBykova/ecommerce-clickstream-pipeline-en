"""PostgreSQL batch tests. Prepared by an agent for joint review."""

import csv
import os
import subprocess
import sys
import tempfile
import unittest
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR / "scripts"))

from load_raw import COPY_SQL, EXPECTED_COLUMNS, execute_script, iter_source_rows, load_batch
from run_pipeline import initialize_database, run_pipeline
from split_source import split_source


BASE = datetime(2020, 1, 1, tzinfo=timezone.utc)
TABLES = (
    "core.users", "core.products", "core.sessions", "core.events",
    "mart.session_funnel", "mart.funnel_summary",
    "mart.session_category_funnel", "mart.category_conversion", "mart.category_data_quality",
)


def event(kind="view", minute=0, session="s1", user=1, product=10, category="electronics.telephone"):
    return (
        (BASE + timedelta(minutes=minute)).strftime("%Y-%m-%d %H:%M:%S UTC"),
        kind, str(product), "100", category, "brand", "100.00", str(user), session,
    )


class BatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.admin_dsn = os.environ.get("TEST_DATABASE_URL")
        if not cls.admin_dsn:
            raise RuntimeError("Set TEST_DATABASE_URL with CREATE DATABASE permission for integration tests")

    def setUp(self):
        self.database_name = "clickstream_test_" + uuid.uuid4().hex
        self.admin = psycopg.connect(self.admin_dsn, autocommit=True)
        self.addCleanup(self.admin.close)
        database_id = sql.Identifier(self.database_name)
        self.admin.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(database_id))
        self.addCleanup(
            self.admin.execute, sql.SQL("DROP DATABASE {} WITH (FORCE)").format(database_id)
        )
        self.dsn = make_conninfo(self.admin_dsn, dbname=self.database_name)
        self.connection = psycopg.connect(self.dsn, autocommit=True)
        self.addCleanup(self.connection.close)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)

    def source(self, name, rows):
        path = self.path / name
        with path.open("w", encoding="utf-8", newline="") as file:
            writer = csv.writer(file)
            writer.writerow(EXPECTED_COLUMNS)
            writer.writerows(rows)
        return path

    def run_batch(self, name, rows, batch_id=None):
        return run_pipeline(self.connection, self.source(name, rows), batch_id)

    def snapshot(self):
        return {
            table: self.connection.execute(f"SELECT * FROM {table} ORDER BY 1, 2").fetchall()
            for table in TABLES
        }

    def counts(self):
        return self.connection.execute("""
            SELECT (SELECT COUNT(*) FROM raw.events_import),
                (SELECT COUNT(*) FROM raw.import_batches),
                (SELECT COUNT(*) FROM core.events)
        """).fetchone()

    def test_late_cart_completes_funnel_and_replay_changes_nothing(self):
        self.run_batch("first.csv", [event(), event("purchase", 10)])
        stage = self.connection.execute("SELECT funnel_stage FROM mart.session_funnel").fetchone()
        self.assertEqual(stage, ("view",))
        result = self.run_batch("late.csv", [event("cart", 5)], "late-cart")
        self.assertEqual((result.inserted_rows, result.affected_sessions), (1, 1))
        stage = self.connection.execute("SELECT funnel_stage FROM mart.session_funnel").fetchone()
        self.assertEqual(stage, ("purchase",))
        before = self.snapshot()
        receipt = self.connection.execute("SELECT * FROM raw.import_batches ORDER BY batch_id").fetchall()
        replay = run_pipeline(self.connection, self.path / "late.csv", "late-cart")
        self.assertEqual((replay.status, replay.inserted_rows, replay.affected_sessions), ("replayed", 0, 0))
        self.assertEqual(self.snapshot(), before)
        after_receipt = self.connection.execute(
            "SELECT * FROM raw.import_batches ORDER BY batch_id"
        ).fetchall()
        self.assertEqual(after_receipt, receipt)

    def test_identical_rows_in_one_batch_are_preserved(self):
        self.run_batch("twice.csv", [event(), event()])
        self.assertEqual(self.counts(), (2, 1, 2))
        self.assertEqual(self.connection.execute("SELECT event_count FROM core.sessions").fetchone(), (2,))
        count = self.connection.execute("SELECT view_sessions FROM mart.category_conversion").fetchone()
        self.assertEqual(count, (1,))

    def test_repacked_zip_and_renamed_csv_have_same_default_identity(self):
        source = self.source("first.csv", [event(), event("cart", 1)])
        original = run_pipeline(self.connection, source)
        archive = self.path / "renamed.zip"
        with zipfile.ZipFile(archive, "w") as file:
            file.write(source, "another-name.csv")
        replay = run_pipeline(self.connection, archive)
        self.assertEqual(original.batch_id, replay.batch_id)
        self.assertEqual(replay.inserted_rows, 0)
        self.assertEqual(self.counts(), (2, 1, 2))

    def test_explicit_different_ids_allow_distinct_identical_deliveries(self):
        self.run_batch("one.csv", [event()], "delivery-one")
        self.run_batch("two.csv", [event()], "delivery-two")
        self.assertEqual(self.counts(), (2, 2, 2))

    def test_changed_content_under_same_id_is_rejected(self):
        self.run_batch("one.csv", [event()], "fixed")
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "changed"):
            self.run_batch("two.csv", [event("cart", 1)], "fixed")
        self.assertEqual(self.counts(), (1, 1, 1))
        self.assertEqual(self.snapshot(), before)

    def test_pending_raw_only_batches_are_applied_together(self):
        initialize_database(self.connection)
        first = self.source("one.csv", [event()])
        second = self.source("two.csv", [event("cart", 1, session="s2")])
        load_batch(self.connection, first)
        load_batch(self.connection, second)
        self.assertEqual(self.counts(), (2, 2, 0))
        result = run_pipeline(self.connection, first)
        self.assertEqual((result.inserted_rows, result.affected_sessions), (0, 2))
        self.assertEqual(self.counts(), (2, 2, 2))
        pending = self.connection.execute(
            "SELECT COUNT(*) FROM raw.import_batches WHERE core_applied_at IS NULL"
        ).fetchone()
        self.assertEqual(pending, (0,))

    def test_legacy_archive_is_adopted_with_original_row_ids(self):
        initialize_database(self.connection)
        source = self.source("legacy.csv", [event(), event("cart", 1)])
        with self.connection.cursor() as cursor:
            with cursor.copy(COPY_SQL) as copy:
                for row in iter_source_rows(source):
                    copy.write_row(row)
            with self.connection.transaction():
                execute_script(cursor, "02_build_core.sql")
        before = self.snapshot()
        raw_ids = self.connection.execute("SELECT raw_event_id FROM raw.events_import ORDER BY 1").fetchall()
        result = run_pipeline(self.connection, source)
        self.assertEqual((result.status, result.inserted_rows, result.adopted_rows), ("adopted", 0, 2))
        self.assertEqual(self.snapshot(), before)
        after_ids = self.connection.execute(
            "SELECT raw_event_id FROM raw.events_import ORDER BY 1"
        ).fetchall()
        self.assertEqual(after_ids, raw_ids)
        self.assertEqual(run_pipeline(self.connection, source).inserted_rows, 0)

    def test_new_batch_cannot_silently_duplicate_unregistered_legacy_rows(self):
        initialize_database(self.connection)
        source = self.source("legacy.csv", [event()])
        with self.connection.cursor() as cursor:
            with cursor.copy(COPY_SQL) as copy:
                for row in iter_source_rows(source):
                    copy.write_row(row)
        with self.assertRaisesRegex(ValueError, "complete original"):
            self.run_batch("other.csv", [event("cart", 1)])
        self.assertEqual(self.counts(), (1, 0, 0))

    def test_malformed_batch_rolls_back_after_valid_first_row(self):
        self.run_batch("good.csv", [event()])
        before = self.snapshot()
        bad_values = {"price": ["NaN", "Infinity", "-1.00", "1.001"], "event_type": ["unknown"]}
        for field, values in bad_values.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    bad = list(event("cart", 1))
                    bad[EXPECTED_COLUMNS.index(field)] = value
                    with self.assertRaises(ValueError):
                        self.run_batch("bad.csv", [event("cart", 1), bad])
                    self.assertEqual(self.counts(), (1, 1, 1))
                    self.assertEqual(self.snapshot(), before)

    def test_core_failure_rolls_back_raw_receipt_and_derived_data(self):
        self.run_batch("good.csv", [event()])
        before = self.snapshot()
        self.connection.execute(
            "ALTER TABLE core.events ADD CONSTRAINT test_reject_cart CHECK (event_type <> 'cart')"
        )
        with self.assertRaises(psycopg.errors.CheckViolation):
            self.run_batch("late.csv", [event("cart", 1)], "late")
        self.assertEqual(self.counts(), (1, 1, 1))
        self.assertEqual(self.snapshot(), before)
        self.connection.execute("ALTER TABLE core.events DROP CONSTRAINT test_reject_cart")
        self.assertEqual(run_pipeline(self.connection, self.path / "late.csv", "late").inserted_rows, 1)

    def test_late_second_user_retracts_session_and_updates_dimension_bounds(self):
        self.run_batch("first.csv", [event(), event(minute=3, session="s2")])
        self.run_batch("late.csv", [event("cart", 1, user=2, product=20)])
        sessions = self.connection.execute("SELECT session_id FROM core.sessions").fetchall()
        self.assertEqual(sessions, [("s2",)])
        expected_time = BASE + timedelta(minutes=3)
        for table in ("core.users", "core.products"):
            bounds = self.connection.execute(f"SELECT first_seen_at, last_seen_at FROM {table}").fetchall()
            self.assertEqual(bounds, [(expected_time, expected_time)])
        self.assertEqual(self.counts(), (3, 2, 1))

    def test_duration_boundary_and_late_invalid_session(self):
        self.run_batch("first.csv", [event(), event("cart", 24 * 60)])
        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM core.sessions").fetchone(), (1,))
        self.run_batch("late.csv", [event("purchase", 24 * 60 + 1)])
        self.assertEqual(self.counts(), (3, 2, 0))
        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM core.users").fetchone(), (0,))
        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM core.products").fetchone(), (0,))

    def test_missing_session_and_category_are_accounted_for(self):
        self.run_batch("source.csv", [event(session=""), event(session="valid", category="")])
        self.assertEqual(self.counts(), (2, 1, 1))
        coverage = self.connection.execute(
            "SELECT total_events, unnamed_category_events FROM mart.category_data_quality"
        ).fetchone()
        self.assertEqual(coverage, (1, 1))
        categories = self.connection.execute("SELECT COUNT(*) FROM mart.category_conversion").fetchone()
        self.assertEqual(categories, (0,))

    def test_incremental_state_equals_full_rebuild(self):
        self.run_batch("first.csv", [event(), event("purchase", 10), event(session="s2", user=2, product=20)])
        self.run_batch("second.csv", [event("cart", 5), event("cart", 1, session="s2", user=3)])
        repeated = event(session="s3", user=1, product=20)
        self.run_batch("third.csv", [repeated, repeated])
        before = self.snapshot()
        with self.connection.transaction(), self.connection.cursor() as cursor:
            execute_script(cursor, "02_build_core.sql")
        self.assertEqual(self.snapshot(), before)

    def test_concurrent_delivery_of_same_batch(self):
        source = self.source("one.csv", [event(), event("cart", 1)])
        barrier = Barrier(2)

        def deliver():
            with psycopg.connect(self.dsn, autocommit=True) as connection:
                barrier.wait(timeout=20)
                return run_pipeline(connection, source, "same-batch")

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: deliver(), range(2)))
        self.assertEqual(sorted(result.inserted_rows for result in results), [0, 2])
        self.assertEqual(self.counts(), (2, 1, 2))

    def test_split_source_and_load_all_parts(self):
        rows = [event(), event("purchase", 10), event("cart", 5), event(session="s2"), event(session="s2")]
        source = self.source("whole.csv", rows)
        parts = split_source(source, self.path / "parts", rows_per_batch=2)
        self.assertEqual(len(parts), 3)
        for part in parts:
            run_pipeline(self.connection, part)
        self.assertEqual(self.counts(), (5, 3, 5))
        before = self.snapshot()
        with self.connection.transaction(), self.connection.cursor() as cursor:
            execute_script(cursor, "02_build_core.sql")
        self.assertEqual(self.snapshot(), before)
        with self.assertRaises(FileExistsError):
            split_source(source, self.path / "parts", rows_per_batch=2)

    def test_cli_bootstraps_database_and_replays(self):
        settings = conninfo_to_dict(self.dsn)
        env_file = self.path / ".env"
        env_file.write_text("\n".join((
            "POSTGRES_HOST=" + settings.get("host", "localhost"),
            "POSTGRES_PORT=" + str(settings.get("port", 5432)),
            "POSTGRES_DB=" + self.database_name,
            "POSTGRES_USER=" + settings["user"],
            "POSTGRES_PASSWORD=" + settings["password"],
        )), encoding="utf-8")
        source = self.source("cli.csv", [event()])
        command = [
            sys.executable, str(PROJECT_DIR / "scripts/run_pipeline.py"),
            "--source", str(source), "--env-file", str(env_file),
        ]
        environment = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        first = subprocess.run(
            command, cwd=PROJECT_DIR, capture_output=True, text=True,
            encoding="utf-8", env=environment,
        )
        self.assertEqual(first.returncode, 0, first.stderr)
        second = subprocess.run(
            command, cwd=PROJECT_DIR, capture_output=True, text=True,
            encoding="utf-8", env=environment,
        )
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn("replayed", second.stdout)
        self.assertEqual(self.counts(), (1, 1, 1))

    def test_empty_batch_is_registered_and_replayed(self):
        first = self.run_batch("empty.csv", [])
        second = run_pipeline(self.connection, self.path / "empty.csv")
        self.assertEqual((first.source_rows, first.affected_sessions), (0, 0))
        self.assertEqual(second.status, "replayed")
        self.assertEqual(self.counts(), (0, 1, 0))

    def test_header_and_record_width_errors_are_atomic(self):
        self.run_batch("good.csv", [event()])
        cases = [
            "wrong,header\n1,2\n",
            ",".join(EXPECTED_COLUMNS) + "\n" + ",".join(event()[:-1]) + "\n",
            ",".join(EXPECTED_COLUMNS) + "\n" + ",".join(event()) + ",extra\n",
        ]
        for content in cases:
            with self.subTest(content=content):
                source = self.path / "bad.csv"
                source.write_text(content, encoding="utf-8")
                with self.assertRaises(ValueError):
                    run_pipeline(self.connection, source)
                self.assertEqual(self.counts(), (1, 1, 1))

    def test_unaffected_session_is_not_rewritten(self):
        self.run_batch("first.csv", [event(), event(session="s2")])
        query = "SELECT xmin::TEXT FROM core.sessions WHERE session_id = 's2'"
        before = self.connection.execute(query).fetchone()
        self.run_batch("late.csv", [event("cart", 1)])
        after = self.connection.execute(query).fetchone()
        self.assertEqual(after, before)

    def test_failed_first_delivery_can_be_retried(self):
        bad = list(event())
        bad[1] = "unknown"
        with self.assertRaises(ValueError):
            self.run_batch("first.csv", [bad], "first")
        self.assertEqual(self.counts(), (0, 0, 0))
        result = self.run_batch("first.csv", [event()], "first")
        self.assertEqual(result.inserted_rows, 1)


if __name__ == "__main__":
    unittest.main()
