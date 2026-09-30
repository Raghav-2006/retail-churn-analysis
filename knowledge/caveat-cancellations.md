---
id: caveat-cancellations
title: How cancellations are recorded
doc_type: data_caveat
owner: Data Engineering
last_updated: 2011-12-10
deprecated: false
---
# Cancellations

- A cancellation is an invoice whose number starts with "C". Its lines are in
  `fact_cancellations` with **negative** quantity and revenue.
- There are 7,901 cancelled invoices. Many reverse sales from an earlier period, and some reverse
  sales from before December 2009 that are not in the data.
- There is no link from a cancellation line to the original sales line, so "which orders were
  cancelled" cannot be answered exactly; cancellations can only be aggregated by period, customer,
  product or country.
