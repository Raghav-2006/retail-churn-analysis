-- Month-over-month retention: share of a month's active customers who buy again the next month.
WITH active AS (
    SELECT DISTINCT customer_id, date_trunc('month', invoice_date)::date AS month
    FROM fact_sales
),
next_month AS (
    SELECT
        a.month,
        count(*)             AS active_customers,
        count(b.customer_id) AS retained_next_month
    FROM active a
    LEFT JOIN active b
        ON b.customer_id = a.customer_id
       AND b.month = a.month + INTERVAL '1 month'
    -- stop two months before the end: the final month is partial
    WHERE a.month < (SELECT max(month) FROM active) - INTERVAL '1 month'
    GROUP BY a.month
)
SELECT *, round(100.0 * retained_next_month / active_customers, 2) AS retention_pct
FROM next_month
ORDER BY month;
