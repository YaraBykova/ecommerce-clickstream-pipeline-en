-- Session-level analytical view.
-- One mart.session_funnel row represents one accepted session.
--
-- The funnel follows chronological order.
--   view -> cart -> purchase.
-- A cart or purchase event without the preceding stage
-- does not count as a funnel transition.

BEGIN;

DROP VIEW IF EXISTS mart.funnel_summary;
DROP VIEW IF EXISTS mart.session_funnel;

CREATE VIEW mart.session_funnel AS
WITH first_views AS (
    SELECT
        session_id,
        MIN(event_time) AS view_at
    FROM core.events
    WHERE event_type = 'view'
    GROUP BY session_id
),
first_carts_after_view AS (
    SELECT
        event.session_id,
        MIN(event.event_time) AS cart_at
    FROM core.events AS event
    JOIN first_views AS first_view
        ON first_view.session_id = event.session_id
       AND event.event_time > first_view.view_at
    WHERE event.event_type = 'cart'
    GROUP BY event.session_id
),
first_purchases_after_cart AS (
    SELECT
        event.session_id,
        MIN(event.event_time) AS purchase_at
    FROM core.events AS event
    JOIN first_carts_after_view AS first_cart
        ON first_cart.session_id = event.session_id
       AND event.event_time > first_cart.cart_at
    WHERE event.event_type = 'purchase'
    GROUP BY event.session_id
)
SELECT
    session.session_id,
    session.user_id,
    session.started_at,
    session.ended_at,
    first_view.view_at,
    first_cart.cart_at,
    first_purchase.purchase_at,
    CASE
        WHEN first_purchase.purchase_at IS NOT NULL THEN 'purchase'
        WHEN first_cart.cart_at IS NOT NULL THEN 'cart'
        WHEN first_view.view_at IS NOT NULL THEN 'view'
        ELSE 'no_view'
    END AS funnel_stage
FROM core.sessions AS session
LEFT JOIN first_views AS first_view
    ON first_view.session_id = session.session_id
LEFT JOIN first_carts_after_view AS first_cart
    ON first_cart.session_id = session.session_id
LEFT JOIN first_purchases_after_cart AS first_purchase
    ON first_purchase.session_id = session.session_id;

CREATE VIEW mart.funnel_summary AS
WITH counts AS (
    SELECT
        COUNT(*) FILTER (WHERE view_at IS NOT NULL) AS view_sessions,
        COUNT(*) FILTER (WHERE cart_at IS NOT NULL) AS cart_sessions,
        COUNT(*) FILTER (WHERE purchase_at IS NOT NULL) AS purchase_sessions
    FROM mart.session_funnel
)
SELECT
    view_sessions,
    cart_sessions,
    purchase_sessions,
    cart_sessions::NUMERIC / NULLIF(view_sessions, 0)
        AS view_to_cart_conversion,
    purchase_sessions::NUMERIC / NULLIF(cart_sessions, 0)
        AS cart_to_purchase_conversion,
    purchase_sessions::NUMERIC / NULLIF(view_sessions, 0)
        AS view_to_purchase_conversion,
    1 - cart_sessions::NUMERIC / NULLIF(view_sessions, 0)
        AS view_to_cart_dropoff,
    1 - purchase_sessions::NUMERIC / NULLIF(cart_sessions, 0)
        AS cart_to_purchase_dropoff
FROM counts;

COMMIT;

-- Quick checks after creating the views.
SELECT *
FROM mart.funnel_summary;

SELECT
    funnel_stage,
    COUNT(*) AS session_count
FROM mart.session_funnel
GROUP BY funnel_stage
ORDER BY session_count DESC;
