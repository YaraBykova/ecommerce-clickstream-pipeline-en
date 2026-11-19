-- Create the raw, core and mart layers.
-- Run once during initial database setup.

CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS core;
CREATE SCHEMA IF NOT EXISTS mart;

-- Raw layer holds a typed copy of CSV records.
-- The original ZIP remains unchanged at data/events.csv.zip.
CREATE TABLE raw.events_import (
    raw_event_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_file_name TEXT NOT NULL,
    event_time TIMESTAMPTZ NOT NULL,
    event_type TEXT NOT NULL,
    product_id BIGINT NOT NULL,
    category_id BIGINT NOT NULL,
    category_code TEXT,
    brand TEXT,
    price NUMERIC(12, 2) NOT NULL,
    user_id BIGINT NOT NULL,
    user_session TEXT,
    loaded_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Core layer holds validated entities for analysis.
CREATE TABLE core.users (
    user_id BIGINT PRIMARY KEY,
    first_seen_at TIMESTAMPTZ NOT NULL,
    last_seen_at TIMESTAMPTZ NOT NULL,
    CHECK (first_seen_at <= last_seen_at)
);

-- The source has no stable catalog of product names.
-- Store the product ID and its first and last occurrence times here,
-- keeping category, brand and price on the event as values at the time of the action.
CREATE TABLE core.products (
    product_id BIGINT PRIMARY KEY,
    first_seen_at TIMESTAMPTZ NOT NULL,
    last_seen_at TIMESTAMPTZ NOT NULL,
    CHECK (first_seen_at <= last_seen_at)
);

-- Only sessions that pass the quality rules enter core.
CREATE TABLE core.sessions (
    session_id TEXT PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES core.users(user_id),
    started_at TIMESTAMPTZ NOT NULL,
    ended_at TIMESTAMPTZ NOT NULL,
    event_count INTEGER NOT NULL CHECK (event_count > 0),
    CHECK (started_at <= ended_at),
    CHECK (ended_at - started_at <= INTERVAL '24 hours'),
    UNIQUE (session_id, user_id)
);

-- Event fact table. Each row links to a specific raw record
-- to preserve traceability of analytical data.
CREATE TABLE core.events (
    raw_event_id BIGINT PRIMARY KEY
        REFERENCES raw.events_import(raw_event_id),
    event_time TIMESTAMPTZ NOT NULL,
    event_type TEXT NOT NULL CHECK (
        event_type IN ('view', 'cart', 'purchase')
    ),
    user_id BIGINT NOT NULL REFERENCES core.users(user_id),
    session_id TEXT NOT NULL,
    product_id BIGINT NOT NULL REFERENCES core.products(product_id),
    category_id BIGINT NOT NULL,
    category_code TEXT,
    brand TEXT,
    price NUMERIC(12, 2) NOT NULL CHECK (price >= 0),
    FOREIGN KEY (session_id, user_id)
        REFERENCES core.sessions(session_id, user_id)
);

-- Indexes for session and event analysis.
CREATE INDEX events_session_time_idx
    ON core.events (session_id, event_time);

CREATE INDEX events_user_time_idx
    ON core.events (user_id, event_time);

CREATE INDEX events_type_time_idx
    ON core.events (event_type, event_time);

-- Analytical views are defined in subsequent SQL scripts.
-- The current views cover session funnels, category conversion and data coverage.
