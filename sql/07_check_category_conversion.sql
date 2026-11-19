-- All mismatch counters must be zero.
-- Prepared by an agent as a draft for joint review.
WITH actual_funnel AS MATERIALIZED (
    SELECT * FROM mart.session_category_funnel
),
event_views AS (
    SELECT
        session_id,
        BTRIM(category_code) AS category_code,
        event_type,
        event_time,
        MIN(event_time) FILTER (WHERE event_type = 'view') OVER (
            PARTITION BY session_id, BTRIM(category_code)
        ) AS view_at
    FROM core.events
    WHERE NULLIF(BTRIM(category_code), '') IS NOT NULL
),
expected_funnel AS (
    SELECT
        session_id,
        category_code,
        MIN(view_at) AS view_at,
        MIN(event_time) FILTER (
            WHERE event_type = 'cart' AND event_time > view_at
        ) AS cart_at
    FROM event_views
    GROUP BY session_id, category_code
    HAVING MIN(view_at) IS NOT NULL
)
SELECT
    COUNT(*) FILTER (
        WHERE actual.session_id IS NULL
    ) AS missing_pairs,
    COUNT(*) FILTER (
        WHERE expected.session_id IS NULL
    ) AS extra_pairs,
    COUNT(*) FILTER (
        WHERE actual.view_at IS DISTINCT FROM expected.view_at
    ) AS view_mismatches,
    COUNT(*) FILTER (
        WHERE actual.cart_at IS DISTINCT FROM expected.cart_at
    ) AS cart_mismatches,
    COUNT(*) FILTER (
        WHERE actual.cart_at <= actual.view_at
    ) AS invalid_event_order,
    (
        SELECT COUNT(*)
        FROM (
            SELECT session_id, category_code
            FROM actual_funnel
            GROUP BY session_id, category_code
            HAVING COUNT(*) > 1
        ) AS duplicates
    ) AS duplicate_pairs,
    (
        SELECT COUNT(*)
        FROM mart.category_conversion
        WHERE cart_sessions > view_sessions
           OR cart_sessions + sessions_without_cart <> view_sessions
    ) AS invalid_counts
FROM expected_funnel AS expected
FULL JOIN actual_funnel AS actual
    USING (session_id, category_code);

SELECT COUNT(*) AS invalid_category_codes
FROM mart.category_conversion
WHERE NULLIF(BTRIM(category_code), '') IS NULL;

-- All core events belong to either named or unknown categories.
SELECT COUNT(*) AS invalid_coverage
FROM mart.category_data_quality
WHERE total_events <> named_category_events + unnamed_category_events
   OR total_events <> (SELECT COUNT(*) FROM core.events)
   OR unnamed_category_share IS DISTINCT FROM
       unnamed_category_events::NUMERIC / NULLIF(total_events, 0);
