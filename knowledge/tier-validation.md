---
id: tier-validation
title: Tier validation results
doc_type: methodology
owner: Customer Analytics
last_updated: 2011-12-01
deprecated: false
---
# How well the tiers predict future revenue

Validation scores customers at 2011-06-01 and measures revenue in the next six months
(2011-06-01 to 2011-11-30).

- Tier 1 (top 10%, 490 customers) earned **58.3%** of next-6-month revenue; mean revenue falls
  monotonically by tier, £5,073 -> £1,007 -> £368 -> £125.
- A monetary-only ranking captured **62.3%**, better than the multi-factor score; learned
  weights reached 62.6%. The multi-factor score is kept for its explanations, not its accuracy.
