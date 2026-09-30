---
id: metric-net-revenue
title: Net revenue (after cancellations)
doc_type: metric_definition
owner: Finance
last_updated: 2011-05-02
deprecated: false
---
# Net revenue

**Net revenue** = gross sales revenue **plus** cancellation revenue over the same period.
`fact_cancellations.revenue` is already negative, so the two sums are added, not subtracted:

    SUM(fact_sales.revenue) + SUM(fact_cancellations.revenue)

Both sums use the same `invoice_date` filter. A cancellation is counted in the period in which it
was raised, even when it reverses a sale from an earlier period.

Do not join `fact_sales` to `fact_cancellations` row by row: the join multiplies lines and
double counts. Aggregate each table separately (two subqueries or CTEs), then combine.

Use net revenue only when a question says "net", "after cancellations/returns", or when a metric
definition requires it (average order value does, see `metric-aov`).
