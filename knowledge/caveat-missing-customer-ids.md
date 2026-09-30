---
id: caveat-missing-customer-ids
title: Missing customer IDs
doc_type: data_caveat
owner: Data Engineering
last_updated: 2011-12-10
deprecated: false
---
# Missing customer IDs

22.8% of raw lines (243,007) had **no customer ID** and were dropped in cleaning: their revenue
cannot be attributed to a customer. Consequences:

- Every table in the warehouse covers **identified customers only**. Warehouse revenue
  (£17.07M) is lower than the raw file's total.
- Guest or walk-in sales cannot be analysed; questions about them cannot be answered.
- Customer counts are counts of identified customers.
