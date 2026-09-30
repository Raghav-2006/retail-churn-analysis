{{ config(indexes=[
    {'columns': ['customer_id']}, {'columns': ['product_code']},
    {'columns': ['date_key']}, {'columns': ['invoiced_at']}]) }}
-- Order-line grain fact table: one row per sales line, keyed to the conformed dimensions.
select
    sales_line_id,
    invoice_id,
    invoiced_at,
    date_key,
    customer_id,
    product_code,
    country,
    quantity,
    unit_price,
    revenue
from {{ ref('stg_sales') }}
