---
id: caveat-non-product-codes
title: Postage, fees and other non-product codes
doc_type: data_caveat
owner: Data Engineering
last_updated: 2011-12-10
deprecated: false
---
# Non-product stock codes

Postage (POST, DOT, C2 carriage), manual adjustments (M, ADJUST), bank charges, commissions (CRUK),
padding lines and test items were **removed from `fact_sales`** in cleaning (2,846 lines).
`dim_product.is_product` is false for the few such codes that still appear on cancellations.

So postage revenue and shipping costs cannot be reported, and every sales line is merchandise.
