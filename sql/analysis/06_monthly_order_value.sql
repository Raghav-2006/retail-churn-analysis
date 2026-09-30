-- Average and median order value by month.
WITH orders AS (
    SELECT invoice, date_trunc('month', min(invoice_date))::date AS month, sum(revenue) AS order_value
    FROM fact_sales
    GROUP BY invoice
)
SELECT
    month,
    count(*)                                                                 AS orders,
    round(avg(order_value), 2)                                               AS avg_order_value,
    round((percentile_cont(0.5) WITHIN GROUP (ORDER BY order_value))::numeric, 2) AS median_order_value
FROM orders
GROUP BY month
ORDER BY month;
