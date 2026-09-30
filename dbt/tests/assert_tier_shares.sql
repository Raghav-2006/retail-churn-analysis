-- Custom test 3: tiers hold 10% / 20% / 30% / 40% of scored customers (to within one customer).
with counts as (
    select tier, count(*) as n, sum(count(*)) over () as total
    from {{ ref('mart_customer_tiers') }} group by tier
),
expected as (
    select * from (values ('Tier 1', 0.10), ('Tier 2', 0.20), ('Tier 3', 0.30), ('Tier 4', 0.40)) as e(tier, share)
)
select e.tier, c.n, c.total, e.share
from expected e left join counts c using (tier)
where c.n is null or abs(c.n - e.share * c.total) > 1
