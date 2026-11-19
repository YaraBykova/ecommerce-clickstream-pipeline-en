-- All results must be zero.
SELECT
    (
        SELECT COUNT(*)
        FROM core.sessions AS s
        LEFT JOIN mart.session_funnel AS f
            ON f.session_id = s.session_id
        WHERE f.session_id IS NULL
    ) AS missing_sessions,

    (
        SELECT COUNT(*)
        FROM mart.session_funnel AS f
        LEFT JOIN core.sessions AS s
            ON s.session_id = f.session_id
        WHERE s.session_id IS NULL
    ) AS extra_sessions,

    (
        SELECT COUNT(*)
        FROM (
            SELECT session_id
            FROM mart.session_funnel
            GROUP BY session_id
            HAVING COUNT(*) > 1
        ) AS duplicates
    ) AS duplicate_sessions,

    (
        SELECT COUNT(*)
        FROM mart.session_funnel
        WHERE
            (
                cart_at IS NOT NULL
                AND (view_at IS NULL OR cart_at <= view_at)
            )
            OR
            (
                purchase_at IS NOT NULL
                AND (cart_at IS NULL OR purchase_at <= cart_at)
            )
    ) AS invalid_event_order;