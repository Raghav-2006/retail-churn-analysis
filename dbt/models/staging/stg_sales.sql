-- Typed, renamed sales lines. No business logic here: that lives in marts.
select
    sales_line_id,
    invoice                        as invoice_id,
    invoice_date                   as invoiced_at,
    date_key,
    customer_id,
    stock_code                     as product_code,
    country,
    quantity::integer              as quantity,
    price::numeric(12, 3)          as unit_price,
    revenue::numeric(14, 3)        as revenue
from {{ source('retail', 'fact_sales') }}
