---
id: metric-aov-legacy
title: Average order value (legacy, gross)
doc_type: metric_definition
owner: Sales Operations
last_updated: 2010-02-15
deprecated: true
superseded_by: metric-aov
---
# Average order value (legacy definition) — DEPRECATED

> Deprecated in April 2011. Use `metric-aov` (net of cancellations).

Legacy AOV was gross revenue divided by the number of orders:

    SUM(fact_sales.revenue) / COUNT(DISTINCT fact_sales.invoice)

It ignored cancellations, so customers who cancelled large orders inflated it.
Kept only so that old dashboards can be reconciled.
