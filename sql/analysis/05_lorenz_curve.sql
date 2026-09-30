-- Cumulative share of revenue vs cumulative share of customers (ranked by revenue).
WITH customer_revenue AS (
    SELECT customer_id, sum(revenue) AS revenue
    FROM fact_sales
    GROUP BY customer_id
)
SELECT
    100.0 * ROW_NUMBER() OVER (ORDER BY revenue DESC) / count(*) OVER () AS customer_pct,
    100.0 * sum(revenue) OVER (ORDER BY revenue DESC ROWS UNBOUNDED PRECEDING)
          / sum(revenue) OVER ()                                         AS cum_revenue_pct
FROM customer_revenue
ORDER BY customer_pct;
