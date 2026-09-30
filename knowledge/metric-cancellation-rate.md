---
id: metric-cancellation-rate
title: Cancellation rate
doc_type: metric_definition
owner: Finance
last_updated: 2011-05-02
deprecated: false
---
# Cancellation rate (official, value based)

**Cancellation rate = cancelled revenue / gross sales revenue x 100**, for the same period.

    100 * -SUM(fact_cancellations.revenue) / SUM(fact_sales.revenue)

- `fact_cancellations.revenue` is negative; negate it so the rate is positive.
- Both sums use the same `invoice_date` period filter; aggregate each table separately.
- Report as a 0-100 percentage. Over the whole dataset it is about 6%.

The rate is value based because a cancelled 1-line invoice and a cancelled 300-line invoice should
not weigh the same. Counting cancelled invoices (about 18% of all invoices) is a different,
unofficial measure; some older sales material still uses it (`sales-team-faq-2010`).

Per-customer cancellation share in the tiering model is a separate feature (`tier-methodology`).
