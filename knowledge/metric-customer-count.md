---
id: metric-customer-count
title: Customer counts
doc_type: metric_definition
owner: Customer Analytics
last_updated: 2011-02-07
deprecated: false
---
# Counting customers

- "Customers" means customers who **bought**: `COUNT(DISTINCT fact_sales.customer_id)` in the period.
- `dim_customer` also lists customers who only ever cancelled; do not count from it unless the
  question asks about all customer records.
- A customer's **home country** (`dim_customer.country`) is the country on their most frequent
  invoices; it can differ from the country of a particular sale.
- Revenue per customer = gross revenue / distinct buying customers in the same period.
