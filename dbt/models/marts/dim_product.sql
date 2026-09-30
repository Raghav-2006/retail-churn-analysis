-- Every product code seen on a sale or a cancellation, with lifetime sales stats.
with sales as (
    select product_code, sum(quantity) as units_sold, sum(revenue) as revenue,
           min(invoiced_at) as first_sold_at, max(invoiced_at) as last_sold_at
    from {{ ref('stg_sales') }}
    group by product_code
)
select
    p.product_code,
    p.description,
    p.is_product,
    coalesce(s.units_sold, 0)      as units_sold,
    coalesce(s.revenue, 0)         as revenue,
    s.first_sold_at,
    s.last_sold_at
from {{ ref('stg_products') }} p
left join sales s using (product_code)
