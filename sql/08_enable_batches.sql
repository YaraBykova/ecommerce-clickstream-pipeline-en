-- Batch delivery metadata. Prepared by an agent for joint review.
-- Repeatable script. Python executes it in a transaction before the first batch.
CREATE TABLE IF NOT EXISTS raw.import_batches (
    batch_id TEXT PRIMARY KEY CHECK (BTRIM(batch_id) <> ''),
    content_sha256 TEXT NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
    source_file_name TEXT NOT NULL,
    source_rows BIGINT NOT NULL CHECK (source_rows >= 0),
    registered_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    core_applied_at TIMESTAMPTZ
);

ALTER TABLE raw.events_import
    ADD COLUMN IF NOT EXISTS batch_id TEXT REFERENCES raw.import_batches(batch_id),
    ADD COLUMN IF NOT EXISTS source_row BIGINT CHECK (source_row > 0);

CREATE INDEX IF NOT EXISTS raw_events_session_idx
    ON raw.events_import (user_session);

-- The final object signals a completed migration to the loader.
CREATE UNIQUE INDEX IF NOT EXISTS events_import_batch_row_key
    ON raw.events_import (batch_id, source_row);
