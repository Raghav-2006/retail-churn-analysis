select
    cancellation_line_id,
    invoice                        as invoice_id,
    invoice_date                   as invoiced_at,
    date_key,
    customer_id,
    stock_code                     as product_code,
    country,
    quantity::integer              as quantity,       -- negative: units returned
    price::numeric(12, 3)          as unit_price,
    revenue::numeric(14, 3)        as revenue         -- negative
from {{ source('retail', 'fact_cancellations') }}
