---
id: metric-order-value
title: Orders and order value
doc_type: metric_definition
owner: Finance
last_updated: 2011-05-02
deprecated: false
---
# Orders and order value

- An **order** is one distinct sales invoice: `COUNT(DISTINCT fact_sales.invoice)`.
  Cancellation invoices (numbers starting with "C") are not orders.
- **Order value** is the gross revenue of one invoice: `SUM(revenue) ... GROUP BY invoice`.
- An order belongs to the period of its `invoice_date`.
- "Basket size" means distinct products (stock codes) on one invoice, not revenue.

For the *average* order value KPI use the definition in `metric-aov`, which is net of
cancellations. For large orders see `metric-large-order`.
