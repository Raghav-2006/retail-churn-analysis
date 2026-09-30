-- Top 3 products by revenue in each of the 5 largest countries.
WITH country_revenue AS (
    SELECT country, sum(revenue) AS country_revenue
    FROM fact_sales
    GROUP BY country
    ORDER BY country_revenue DESC
    LIMIT 5
),
product_revenue AS (
    SELECT s.country, s.stock_code, sum(s.revenue) AS revenue
    FROM fact_sales s
    JOIN country_revenue c USING (country)
    GROUP BY s.country, s.stock_code
),
ranked AS (
    SELECT
        *,
        ROW_NUMBER() OVER (PARTITION BY country ORDER BY revenue DESC) AS rn
    FROM product_revenue
)
SELECT r.country, r.rn AS rank, r.stock_code, p.description, round(r.revenue, 2) AS revenue
FROM ranked r
JOIN dim_product p USING (stock_code)
JOIN country_revenue c USING (country)
WHERE r.rn <= 3
ORDER BY c.country_revenue DESC, r.rn;
