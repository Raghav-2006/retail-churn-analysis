---
id: metric-international-revenue
title: Domestic vs international revenue
doc_type: metric_definition
owner: Finance
last_updated: 2011-03-14
deprecated: false
---
# Domestic and international revenue

- **Domestic** revenue: `fact_sales.country = 'United Kingdom'`.
- **International** revenue: every other country **except `'Unspecified'`**. Lines with an
  unspecified country cannot be assigned to a market and are excluded from both.
- EIRE (Ireland), Channel Islands and "European Community" are international.
- The country of a sale is the country on the invoice line (`fact_sales.country`), not the
  customer's home country in `dim_customer`.

Gross revenue as in `metric-revenue`.
