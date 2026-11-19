-- Read-only data quality checks.

-- 1. Counts of source, accepted and excluded events.
SELECT
    (SELECT COUNT(*) FROM raw.events_import) AS raw_events,
    (SELECT COUNT(*) FROM core.events) AS core_events,
    (SELECT COUNT(*) FROM raw.events_import)
        - (SELECT COUNT(*) FROM core.events) AS excluded_events;


-- 2. Compare session records with the loaded events.
-- All three results must be zero.
WITH actual_sessions AS (
    SELECT
        session_id,
        MIN(user_id) AS user_id,
        COUNT(DISTINCT user_id) AS user_count,
        MIN(event_time) AS started_at,
        MAX(event_time) AS ended_at,
        COUNT(*) AS event_count
    FROM core.events
    GROUP BY session_id
)
SELECT
    COUNT(*) FILTER (
        WHERE s.event_count IS DISTINCT FROM a.event_count
    ) AS event_count_mismatches,

    COUNT(*) FILTER (
        WHERE s.started_at IS DISTINCT FROM a.started_at
           OR s.ended_at IS DISTINCT FROM a.ended_at
    ) AS time_mismatches,

    COUNT(*) FILTER (
        WHERE s.user_id IS DISTINCT FROM a.user_id
           OR a.user_count IS DISTINCT FROM 1::BIGINT
    ) AS user_mismatches
FROM core.sessions AS s
LEFT JOIN actual_sessions AS a
    ON s.session_id = a.session_id;


-- 3. Reasons for excluding events.
-- Reasons may overlap, so their counts cannot simply be added.
WITH session_quality AS (
    SELECT
        user_session,
        COUNT(DISTINCT user_id) AS user_count,
        MAX(event_time) - MIN(event_time) AS duration
    FROM raw.events_import
    WHERE user_session IS NOT NULL
      AND BTRIM(user_session) <> ''
    GROUP BY user_session
)
SELECT
    COUNT(*) FILTER (
        WHERE e.user_session IS NULL
           OR BTRIM(e.user_session) = ''
    ) AS missing_session_id,

    COUNT(*) FILTER (
        WHERE q.user_count > 1
    ) AS multi_user_session_events,

    COUNT(*) FILTER (
        WHERE q.duration > INTERVAL '24 hours'
    ) AS long_session_events,

    COUNT(*) FILTER (
        WHERE c.raw_event_id IS NULL
    ) AS total_excluded_events
FROM raw.events_import AS e
LEFT JOIN session_quality AS q
    ON e.user_session = q.user_session
LEFT JOIN core.events AS c
    ON e.raw_event_id = c.raw_event_id;


-- 4. Accepted events by type.
SELECT
    event_type,
    COUNT(*) AS event_count
FROM core.events
GROUP BY event_type
ORDER BY event_count DESC;