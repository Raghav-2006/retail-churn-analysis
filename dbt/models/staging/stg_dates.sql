select
    date_key,
    full_date,
    year::integer                  as year,
    quarter::integer               as quarter,
    month::integer                 as month,
    month_name,
    month_start,
    day_of_month::integer          as day_of_month,
    day_of_week::integer           as day_of_week,
    day_name,
    is_weekend
from {{ source('retail', 'dim_date') }}
