---
id: metric-large-order
title: Large order
doc_type: metric_definition
owner: Finance
last_updated: 2011-05-02
deprecated: false
---
# Large order

A **large order** is an order (one sales invoice) whose gross order value is **at least £1,000**.

    SELECT invoice FROM fact_sales WHERE <period> GROUP BY invoice HAVING SUM(revenue) >= 1000

The threshold was raised from £500 to £1,000 in May 2011 so that "large" means the top few
percent of orders. Older sales material with £500 is out of date.
