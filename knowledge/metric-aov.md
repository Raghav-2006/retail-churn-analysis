---
id: metric-aov
title: Average order value (AOV)
doc_type: metric_definition
owner: Finance
last_updated: 2011-04-11
deprecated: false
---
# Average order value (AOV), official KPI since April 2011

**AOV = net revenue in the period / number of orders in the period.**

- Net revenue = `SUM(fact_sales.revenue) + SUM(fact_cancellations.revenue)` for the period
  (cancellation revenue is negative). See `metric-net-revenue`.
- Number of orders = `COUNT(DISTINCT fact_sales.invoice)` in the same period.
- Aggregate the two fact tables separately before dividing; never join them.

This definition applies to the KPI called **average order value (AOV)**. Other per-order
averages, e.g. "average revenue per order" by country, or a mean or median order value where the
question defines the order value, use gross revenue (`metric-revenue`) unless they say net.

Why net: the board asked for AOV to reflect money actually kept. The earlier gross-revenue AOV
(`metric-aov-legacy`) is deprecated and overstated AOV by about 7%.
