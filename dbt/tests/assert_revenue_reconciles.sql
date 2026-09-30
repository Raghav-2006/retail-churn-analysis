-- Custom test 1: revenue reconciliation. The mart must carry exactly the revenue of the
-- source landing table, overall and for every month; a dropped batch, a double-counting
-- join or a type truncation in the dbt layer returns a failing row here.
with src as (
    select date_trunc('month', invoice_date) as month, sum(revenue) as revenue
    from {{ source('retail', 'fact_sales') }} group by 1
),
mart as (
    select date_trunc('month', invoiced_at) as month, sum(revenue) as revenue
    from {{ ref('fct_sales') }} group by 1
),
by_month as (
    select coalesce(s.month, m.month) as month, s.revenue as source_revenue, m.revenue as mart_revenue
    from src s full outer join mart m using (month)
)
select * from by_month
where source_revenue is null or mart_revenue is null or abs(source_revenue - mart_revenue) >= 0.005
union all
select null, (select sum(revenue) from {{ source('retail', 'fact_sales') }}),
       (select sum(revenue) from {{ ref('fct_sales') }})
where abs((select sum(revenue) from {{ source('retail', 'fact_sales') }})
          - (select sum(revenue) from {{ ref('fct_sales') }})) >= 0.005
