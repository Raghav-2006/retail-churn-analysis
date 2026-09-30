-- One row per customer on either fact table. Country = the customer's most frequent invoice
-- country (ties -> alphabetically first), the same rule the Python loader uses.
with lines as (
    select customer_id, country from {{ ref('stg_sales') }}
    union all
    select customer_id, country from {{ ref('stg_cancellations') }}
),
country_rank as (
    select customer_id, country,
           row_number() over (partition by customer_id order by count(*) desc, country) as rn
    from lines
    group by customer_id, country
),
sales as (
    select customer_id,
           min(invoiced_at)              as first_purchase_at,
           max(invoiced_at)              as last_purchase_at,
           count(distinct invoice_id)    as orders,
           sum(revenue)                  as lifetime_revenue
    from {{ ref('stg_sales') }}
    group by customer_id
)
select
    c.customer_id,
    c.country,
    s.first_purchase_at,
    s.last_purchase_at,
    coalesce(s.orders, 0)                as orders,
    coalesce(s.lifetime_revenue, 0)      as lifetime_revenue
from country_rank c
left join sales s using (customer_id)
where c.rn = 1
