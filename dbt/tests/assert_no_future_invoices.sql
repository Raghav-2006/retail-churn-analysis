-- Custom test 2: no invoice may be dated in the future (a clock or timezone bug upstream,
-- or a test fixture leaking into production, would show up here).
select 'sales' as fact, sales_line_id as line_id, invoiced_at
from {{ ref('fct_sales') }}
where invoiced_at > current_timestamp
union all
select 'cancellations', cancellation_line_id, invoiced_at
from {{ ref('fct_cancellations') }}
where invoiced_at > current_timestamp
