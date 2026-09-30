-- Top 10 countries by revenue, with their share of the total.
SELECT
    country,
    round(sum(revenue), 2)                                   AS revenue,
    count(DISTINCT customer_id)                              AS customers,
    round(100 * sum(revenue) / sum(sum(revenue)) OVER (), 2) AS pct_of_total
FROM fact_sales
GROUP BY country
ORDER BY revenue DESC
LIMIT 10;
