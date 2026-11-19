BEGIN;

-- Full rebuilds and batch loads share the same lock.
SELECT pg_advisory_xact_lock(7301926);

TRUNCATE TABLE
    core.events,
    core.sessions,
    core.products,
    core.users;

-- Select sessions using the project rules.
CREATE TEMP TABLE valid_sessions ON COMMIT DROP AS
SELECT
    user_session AS session_id,
    MIN(user_id) AS user_id,
    MIN(event_time) AS started_at,
    MAX(event_time) AS ended_at,
    COUNT(*)::INTEGER AS event_count
FROM raw.events_import
WHERE user_session IS NOT NULL
  AND BTRIM(user_session) <> ''
GROUP BY user_session
HAVING COUNT(DISTINCT user_id) = 1
   AND MAX(event_time) - MIN(event_time)
       <= INTERVAL '24 hours';

-- Users participating in accepted sessions.
INSERT INTO core.users (
    user_id,
    first_seen_at,
    last_seen_at
)
SELECT
    e.user_id,
    MIN(e.event_time),
    MAX(e.event_time)
FROM raw.events_import AS e
JOIN valid_sessions AS s
    ON e.user_session = s.session_id
GROUP BY e.user_id;

-- Products occurring in accepted sessions.
INSERT INTO core.products (
    product_id,
    first_seen_at,
    last_seen_at
)
SELECT
    e.product_id,
    MIN(e.event_time),
    MAX(e.event_time)
FROM raw.events_import AS e
JOIN valid_sessions AS s
    ON e.user_session = s.session_id
GROUP BY e.product_id;

-- Accepted sessions.
INSERT INTO core.sessions (
    session_id,
    user_id,
    started_at,
    ended_at,
    event_count
)
SELECT
    session_id,
    user_id,
    started_at,
    ended_at,
    event_count
FROM valid_sessions;

-- Events from accepted sessions, linked to source records.
INSERT INTO core.events (
    raw_event_id,
    event_time,
    event_type,
    user_id,
    session_id,
    product_id,
    category_id,
    category_code,
    brand,
    price
)
SELECT
    e.raw_event_id,
    e.event_time,
    e.event_type,
    e.user_id,
    s.session_id,
    e.product_id,
    e.category_id,
    e.category_code,
    e.brand,
    e.price
FROM raw.events_import AS e
JOIN valid_sessions AS s
    ON e.user_session = s.session_id;

COMMIT;

-- Inspect the result.
SELECT
    (SELECT COUNT(*) FROM raw.events_import) AS raw_events,
    (SELECT COUNT(*) FROM core.users) AS core_users,
    (SELECT COUNT(*) FROM core.products) AS core_products,
    (SELECT COUNT(*) FROM core.sessions) AS core_sessions,
    (SELECT COUNT(*) FROM core.events) AS core_events;
