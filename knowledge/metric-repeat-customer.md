---
id: metric-repeat-customer
title: Repeat customer and repeat rate
doc_type: metric_definition
owner: Customer Analytics
last_updated: 2011-02-07
deprecated: false
---
# Repeat customers

A **repeat customer in a period** placed **two or more orders** (distinct sales invoices) within
that period.

**Repeat rate** (percent) = repeat customers / customers with at least one order in the period x 100.

Both counts use the same period filter on `fact_sales.invoice_date`. A customer whose second order
falls in the next period is not a repeat customer for this period.
