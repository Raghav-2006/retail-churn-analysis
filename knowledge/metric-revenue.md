---
id: metric-revenue
title: Revenue (gross sales)
doc_type: metric_definition
owner: Finance
last_updated: 2011-05-02
deprecated: false
---
# Revenue (gross sales)

**Revenue**, "sales", "spend" and "turnover" all mean **gross sales revenue**: the sum of
`fact_sales.revenue` (quantity x unit price, GBP) over the period.

- Cancellations are **not** subtracted. Cancelled lines live in `fact_cancellations` and are
  reported separately (see `metric-net-revenue` and `metric-cancellation-rate`).
- Postage, bank charges, manual adjustments and test codes are not revenue; they were removed in
  cleaning, so every row in `fact_sales` is merchandise.
- Periods are filtered on `invoice_date` with half-open ranges, e.g.
  `invoice_date >= '2011-01-01' AND invoice_date < '2012-01-01'`.

SQL: `SELECT SUM(revenue) FROM fact_sales WHERE <period filter>`.
