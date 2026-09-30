---
id: metric-churn
title: Churned customer and churn rate
doc_type: metric_definition
owner: Customer Analytics
last_updated: 2011-06-20
deprecated: false
---
# Churn (current definition)

A customer has **churned as of date D** if they placed at least one order before D + 1 day, but
**no order in the 90 days ending on D** — i.e. they have ordered before but are not active
(`metric-active-customer`).

    last_order_date = MAX(fact_sales.invoice_date) over orders with invoice_date < D + 1 day
    churned  <=>  last_order_date < (D + 1 day) - INTERVAL '90 days'

**Churn rate as of D** (percent) = churned customers / all customers with at least one order
before D + 1 day x 100. Report it as a 0-100 percentage.

"As of the end of 2010" means D = 2010-12-31, so the cut-off is `DATE '2011-01-01' - 90`.

This replaced the 365-day definition (`metric-churn-2010`, deprecated) in June 2011, to align churn
with the 90-day active window: every customer who has ordered is exactly one of active or churned.
