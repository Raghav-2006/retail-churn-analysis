-- Share of revenue from the top 1%, 10% and 20% of customers.
WITH customer_revenue AS (
    SELECT customer_id, sum(revenue) AS revenue
    FROM fact_sales
    GROUP BY customer_id
),
ranked AS (
    SELECT
        revenue,
        ROW_NUMBER() OVER (ORDER BY revenue DESC) AS rn,
        count(*)     OVER ()                      AS n,
        sum(revenue) OVER ()                      AS total
    FROM customer_revenue
)
SELECT
    any_value(n)                                                                        AS customers,
    round(100 * sum(revenue) FILTER (WHERE rn <= ceil(0.01 * n)) / any_value(total), 2) AS top_1pct_share,
    round(100 * sum(revenue) FILTER (WHERE rn <= ceil(0.10 * n)) / any_value(total), 2) AS top_10pct_share,
    round(100 * sum(revenue) FILTER (WHERE rn <= ceil(0.20 * n)) / any_value(total), 2) AS top_20pct_share
FROM ranked;
