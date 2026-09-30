{{ config(indexes=[{'columns': ['customer_id'], 'unique': true}]) }}
{%- set w = var('tier_weights') -%}
{%- set total = w.values() | sum -%}
{%- set features = ['recency', 'frequency', 'monetary', 'tenure', 'avg_order_value', 'breadth',
                    'cancel_rate', 'regularity'] -%}
{%- set lower_is_better = ['recency', 'cancel_rate', 'regularity'] -%}
-- SQL twin of tiering/score.py: percentile-rank each feature (1 = best), weighted sum -> score,
-- then 10/20/30/40% tiers by score rank (ties broken by customer_id). tiering/verify_dbt.py
-- asserts this matches the Python implementation and the frozen v1 output customer by customer.
with f as (
    select * from {{ ref('mart_customer_features') }}
    where cutoff_date = date '{{ var("tier_cutoff") }}'
),
pct as (
    select
        customer_id,
        {% for col in features -%}
        {{ pct_rank(col, col not in lower_is_better) }} as pct_{{ col }}{{ ',' if not loop.last }}
        {% endfor %}
    from f
),
scored as (
    select
        customer_id,
        {% for col in features -%}
        pct_{{ col }},
        {% endfor -%}
        {% for col in features -%}
        pct_{{ col }} * ({{ w[col] }}::float8 / {{ total }}::float8) as contrib_{{ col }},
        {% endfor -%}
        {#- Same left-to-right summation order as pandas, so the float result is identical. -#}
        {% for col in features -%}
        pct_{{ col }} * ({{ w[col] }}::float8 / {{ total }}::float8){{ ' +' if not loop.last }}
        {% endfor %} as score
    from pct
),
ranked as (
    select *,
           row_number() over (order by score desc, customer_id) as score_rank,
           count(*) over ()                                     as n
    from scored
)
select
    customer_id,
    score,
    score_rank,
    case
        when score_rank::float8 / n <= 0.10 + 1e-12 then 'Tier 1'
        when score_rank::float8 / n <= 0.30 + 1e-12 then 'Tier 2'
        when score_rank::float8 / n <= 0.60 + 1e-12 then 'Tier 3'
        else 'Tier 4'
    end as tier,
    {% for col in features -%}
    pct_{{ col }},
    contrib_{{ col }}{{ ',' if not loop.last }}
    {% endfor %}
from ranked
