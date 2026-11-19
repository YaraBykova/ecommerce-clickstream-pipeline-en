-- View-to-cart transitions for nonempty category_code values.
-- Prepared by an agent as a draft for joint review.
-- One mart.session_category_funnel row represents one session-category pair
-- with at least one view. The viewed and carted products may differ.
-- Different category_id values with the same category_code are merged within a session.
-- Events with no category name are counted separately in category_data_quality.

BEGIN;

DROP VIEW IF EXISTS mart.category_conversion;
DROP VIEW IF EXISTS mart.session_category_funnel;
DROP VIEW IF EXISTS mart.category_data_quality;

CREATE VIEW mart.session_category_funnel AS
WITH first_views AS (
    SELECT
        session_id,
        BTRIM(category_code) AS category_code,
        MIN(event_time) AS view_at
    FROM core.events
    WHERE event_type = 'view'
      AND NULLIF(BTRIM(category_code), '') IS NOT NULL
    GROUP BY session_id, BTRIM(category_code)
)
SELECT
    first_view.session_id,
    first_view.category_code,
    first_view.view_at,
    MIN(cart.event_time) AS cart_at
FROM first_views AS first_view
LEFT JOIN core.events AS cart
    ON cart.session_id = first_view.session_id
   AND BTRIM(cart.category_code) = first_view.category_code
   AND cart.event_type = 'cart'
   AND cart.event_time > first_view.view_at
GROUP BY
    first_view.session_id,
    first_view.category_code,
    first_view.view_at;

CREATE VIEW mart.category_conversion AS
SELECT
    category_code,
    COUNT(*) AS view_sessions,
    COUNT(cart_at) AS cart_sessions,
    COUNT(*) - COUNT(cart_at) AS sessions_without_cart,
    COUNT(cart_at)::NUMERIC / NULLIF(COUNT(*), 0)
        AS view_to_cart_conversion
FROM mart.session_category_funnel
GROUP BY category_code;

CREATE VIEW mart.category_data_quality AS
SELECT
    COUNT(*) AS total_events,
    COUNT(*) FILTER (
        WHERE NULLIF(BTRIM(category_code), '') IS NOT NULL
    ) AS named_category_events,
    COUNT(*) FILTER (
        WHERE NULLIF(BTRIM(category_code), '') IS NULL
    ) AS unnamed_category_events,
    COUNT(*) FILTER (
        WHERE NULLIF(BTRIM(category_code), '') IS NULL
    )::NUMERIC / NULLIF(COUNT(*), 0) AS unnamed_category_share
FROM core.events;

COMMIT;

-- Top ten categories by sessions without a subsequent cart event.
-- A threshold of 1,000 viewing sessions focuses the table on larger groups.
SELECT
    category_code,
    view_sessions,
    cart_sessions,
    sessions_without_cart,
    ROUND(100 * view_to_cart_conversion, 2) AS conversion_percent
FROM mart.category_conversion
WHERE view_sessions >= 1000
ORDER BY sessions_without_cart DESC, category_code
LIMIT 10;

-- Candidates for further review are larger groups with conversion
-- below the pooled transition rate across session-category pairs.
-- Ranking by sessions without a cart event determines the review priority.
WITH baseline AS (
    SELECT
        SUM(cart_sessions)::NUMERIC / NULLIF(SUM(view_sessions), 0)
            AS conversion
    FROM mart.category_conversion
)
SELECT
    category.category_code,
    category.view_sessions,
    category.cart_sessions,
    category.sessions_without_cart,
    ROUND(100 * category.view_to_cart_conversion, 2) AS conversion_percent,
    ROUND(100 * baseline.conversion, 2) AS baseline_percent
FROM mart.category_conversion AS category
CROSS JOIN baseline
WHERE category.view_sessions >= 1000
  AND category.view_to_cart_conversion < baseline.conversion
ORDER BY category.sessions_without_cart DESC, category.category_code
LIMIT 5;

-- Coverage of the analysis across core events. Events with unknown categories
-- remain in core and contribute to the overall store funnel.
SELECT * FROM mart.category_data_quality;
