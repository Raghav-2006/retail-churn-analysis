---
id: table-fact-cancellations
title: "Table: fact_cancellations"
doc_type: table
owner: Data Engineering
last_updated: 2011-12-10
deprecated: false
---
# fact_cancellations

One row per cancelled invoice line (invoice starts with "C"). Same columns as `fact_sales`, with
`cancellation_line_id` as the key, and:

- `quantity` is **negative**, `revenue` is **negative** (quantity x price);
- `invoice` is the cancellation invoice number, not the original order's number.

Aggregate it separately from `fact_sales` and combine the totals; a row-level join double counts.
