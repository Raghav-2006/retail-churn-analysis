---
id: metric-active-customer
title: Active customer
doc_type: metric_definition
owner: Customer Analytics
last_updated: 2011-06-20
deprecated: false
---
# Active customer (current definition)

There are two official uses of "active", depending on how the question is phrased.

**1. Active *in* a period** ("active customers in November 2011", "per active customer in 2011"):
a customer with **at least one order in that period**. Count `DISTINCT fact_sales.customer_id`
with the period filter on `invoice_date`.

**2. Active *as of* a date D** ("at the end of Q3", "as of 30 June", "currently active"): a
customer with **at least one order in the 90 days ending on D** (D included):

    invoice_date >= (D + 1 day) - INTERVAL '90 days'  AND  invoice_date < D + 1 day

- "At the end of a month/quarter/year" means D = the last day of that period. Example, end of
  Q2 2011: `invoice_date >= DATE '2011-07-01' - 90 AND invoice_date < '2011-07-01'`
  (from 2 April 2011).
- Cancellations do not make a customer active.
- The 90-day window replaced the 180-day window in June 2011 (`metric-active-customer-2009`,
  deprecated) because most wholesalers reorder within a quarter.

Customers who have ordered before D but are not active as of D are churned (`metric-churn`).
