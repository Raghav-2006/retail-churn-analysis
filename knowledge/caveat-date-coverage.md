---
id: caveat-date-coverage
title: Date coverage and the partial December 2011
doc_type: data_caveat
owner: Data Engineering
last_updated: 2011-12-10
deprecated: false
---
# Date coverage

- Sales run from **2009-12-01 to 2011-12-09**. 2010 is the only complete calendar year with a
  full year before it.
- **December 2011 is partial**: 9 of 31 days. Do not compare it with other months, and treat
  2011 totals as "2011 to 9 December".
- December 2009 is the first month, so anything that needs history (new customers, churn,
  month-over-month growth) is unreliable at the start of the data.
- Timestamps are local UK time, minute precision. Filter periods with half-open ranges on
  `invoice_date`.
