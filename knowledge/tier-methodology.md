---
id: tier-methodology
title: Customer tiering methodology
doc_type: methodology
owner: Customer Analytics
last_updated: 2011-06-01
deprecated: false
---
# Customer tiers

Customers are scored on 8 features computed at a cutoff date (scoring cutoff 2011-06-01), each
turned into a 0-1 percentile rank so that 1 is best:

| feature | weight | note |
|---|---|---|
| recency (days since last order) | 0.25 | lower is better |
| frequency (orders) | 0.25 | |
| monetary (revenue to date) | 0.30 | |
| breadth (distinct products) | 0.10 | |
| cancel_rate (share of orders cancelled) | 0.10 | lower is better |
| tenure, avg order value, regularity | 0 | shown in explanations only |

score = sum of weight x percentile. Tiers by score rank: **Tier 1 top 10%, Tier 2 next 20%,
Tier 3 next 30%, Tier 4 bottom 40%**. Ties are broken by customer_id.

Tiers are stored in `analytics.mart_customer_tiers` (dbt), which the SQL analyst cannot query;
the analyst's warehouse has no tier column.
