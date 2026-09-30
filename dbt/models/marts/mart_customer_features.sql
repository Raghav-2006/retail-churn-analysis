{{ config(indexes=[{'columns': ['cutoff_date', 'customer_id'], 'unique': true}]) }}
-- The 8 tiering features, one row per (cutoff_date, customer). Each snapshot only sees
-- transactions strictly before its cutoff, so no feature can leak the outcome window.
-- Expressions (and casts) are identical to the v1 SQL in tiering/features.py, so values match v1 exactly.
with cutoffs as (
    {% for c in var('feature_cutoffs') -%}
    select date '{{ c }}' as cutoff_date {% if not loop.last %}union all{% endif %}
    {% endfor %}
),
orders as (          -- one row per invoice
    select c.cutoff_date, s.customer_id, s.invoice_id,
           min(s.invoiced_at) as order_ts, sum(s.revenue) as order_value
    from {{ ref('fct_sales') }} s
    join cutoffs c on s.invoiced_at < c.cutoff_date
    group by c.cutoff_date, s.customer_id, s.invoice_id
),
gaps as (            -- days between consecutive orders
    select cutoff_date, customer_id,
           order_ts::date - lag(order_ts::date) over (
               partition by cutoff_date, customer_id order by order_ts) as gap_days
    from orders
),
customer as (
    select cutoff_date, customer_id, min(order_ts) as first_ts, max(order_ts) as last_ts,
           count(*) as frequency, sum(order_value) as monetary
    from orders
    group by cutoff_date, customer_id
),
breadth as (
    select c.cutoff_date, s.customer_id, count(distinct s.product_code) as breadth
    from {{ ref('fct_sales') }} s
    join cutoffs c on s.invoiced_at < c.cutoff_date
    group by c.cutoff_date, s.customer_id
),
cancels as (
    select c.cutoff_date, x.customer_id, count(distinct x.invoice_id) as cancelled_orders
    from {{ ref('fct_cancellations') }} x
    join cutoffs c on x.invoiced_at < c.cutoff_date
    group by c.cutoff_date, x.customer_id
),
regularity as (      -- needs at least 3 orders (2 gaps) for a standard deviation
    select cutoff_date, customer_id, stddev_samp(gap_days) as regularity
    from gaps
    group by cutoff_date, customer_id
)
select
    c.cutoff_date,
    c.customer_id,
    (c.cutoff_date - c.last_ts::date)                                   as recency,
    c.frequency,
    c.monetary::float                                                   as monetary,
    (c.cutoff_date - c.first_ts::date)                                  as tenure,
    (c.monetary / c.frequency)::float                                   as avg_order_value,
    b.breadth,
    coalesce(x.cancelled_orders, 0)::float
        / (c.frequency + coalesce(x.cancelled_orders, 0))               as cancel_rate,
    r.regularity::float                                                 as regularity
from customer c
join breadth b using (cutoff_date, customer_id)
left join cancels x using (cutoff_date, customer_id)
left join regularity r using (cutoff_date, customer_id)
