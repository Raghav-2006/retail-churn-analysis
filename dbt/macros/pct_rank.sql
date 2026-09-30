{#-
  0-1 percentile rank, identical to pandas Series.rank(pct=True, method="average", na_option="keep")
  followed by fillna(0): ties share the average of their ranks, NULLs rank 0 (worst), and the
  denominator counts non-NULL values only. higher_is_better=false inverts the ordering so 1 is best.
  Computed in float8 exactly as pandas does, so scores match the Python implementation bit for bit.
-#}
{% macro pct_rank(col, higher_is_better=true) -%}
    case when {{ col }} is null then 0.0::float8 else
        (rank() over (partition by ({{ col }} is null) order by {{ col }} {{ 'asc' if higher_is_better else 'desc' }})
         + (count(*) over (partition by {{ col }}) - 1) / 2.0)::float8
        / (count({{ col }}) over ())::float8
    end
{%- endmacro %}
