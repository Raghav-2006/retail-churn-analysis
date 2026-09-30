---
id: metric-new-customer
title: New customer (acquisition)
doc_type: metric_definition
owner: Customer Analytics
last_updated: 2011-02-07
deprecated: false
---
# New customers

A customer is **new in a period** if their **first ever order** falls in that period:

    first_order_date = MIN(fact_sales.invoice_date) per customer, over ALL data
    new in period    <=>  first_order_date within the period

- Compute the first order over the whole table, then filter; filtering first would make every
  customer look new.
- A cancellation is not an order, so it cannot make a customer new.
- The data starts on 2009-12-01, so every customer looks "new" in December 2009. Treat new-customer
  counts before 2010-12 as unreliable (see `caveat-date-coverage`).
