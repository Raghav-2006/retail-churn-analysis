---
id: table-dim-product
title: "Table: dim_product"
doc_type: table
owner: Data Engineering
last_updated: 2011-12-10
deprecated: false
---
# dim_product

One row per stock code on either fact table.

| column | meaning |
|---|---|
| stock_code | primary key |
| description | most common free-text description of the code |
| is_product | false for postage/fee/adjustment codes that appear only on cancellations |

There is no category, colour, size, cost or supplier column. "Product" questions group by
`stock_code`, and show `description` for readability.
