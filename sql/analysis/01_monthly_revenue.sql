-- Monthly revenue, orders and active customers.
-- The dataset stops on 2011-12-09, so only its final month is incomplete.
SELECT
    date_trunc('month', invoice_date)::date AS month,
    round(sum(revenue), 2)                  AS revenue,
    count(DISTINCT invoice)                 AS orders,
    count(DISTINCT customer_id)             AS active_customers,
    date_trunc('month', invoice_date) = (SELECT date_trunc('month', max(invoice_date)) FROM fact_sales) AS is_partial
FROM fact_sales
GROUP BY 1, 5
ORDER BY 1;
