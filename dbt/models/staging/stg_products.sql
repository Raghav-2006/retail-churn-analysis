select
    stock_code                     as product_code,
    description,
    is_product
from {{ source('retail', 'dim_product') }}
