---
id: rfm-segments
title: RFM segments
doc_type: methodology
owner: Customer Analytics
last_updated: 2011-12-10
deprecated: false
---
# RFM segments

RFM segments score recency, frequency and monetary value into quintiles at a snapshot of
2011-12-10 and group customers into named segments: Champions, Loyal, Potential, At-Risk,
Hibernating and others. Champions are 25% of customers and 69% of revenue.

Segments are an analysis output (`notebooks/`, `metrics/03_rfm.json`), not a warehouse table,
so the SQL analyst cannot filter by segment. They are not the same as tiers (`tier-methodology`).
