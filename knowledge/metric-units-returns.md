---
id: metric-units-returns
title: Units sold and returns
doc_type: metric_definition
owner: Operations
last_updated: 2010-11-02
deprecated: false
---
# Units and returns

- **Units sold** = `SUM(fact_sales.quantity)`.
- **Returns** are cancellations. **Units returned** = `-SUM(fact_cancellations.quantity)`
  (quantities are negative in `fact_cancellations`).
- **Return rate by units** = units returned / units sold x 100 over the same period. This is an
  operations measure; the finance KPI is `metric-cancellation-rate` (value based).
