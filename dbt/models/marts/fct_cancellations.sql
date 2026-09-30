{{ config(indexes=[{'columns': ['customer_id']}, {'columns': ['invoiced_at']}]) }}
select
    cancellation_line_id,
    invoice_id,
    invoiced_at,
    date_key,
    customer_id,
    product_code,
    country,
    quantity,
    unit_price,
    revenue
from {{ ref('stg_cancellations') }}
