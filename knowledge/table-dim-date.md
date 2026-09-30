---
id: table-dim-date
title: "Table: dim_date"
doc_type: table
owner: Data Engineering
last_updated: 2011-12-10
deprecated: false
---
# dim_date

One row per calendar day from 2009-12-01 to 2011-12-09 (739 rows). Join on `date_key` (YYYYMMDD).

Columns: `full_date`, `year`, `quarter`, `month`, `month_name`, `month_start`, `day_of_month`,
`day_of_week` (1 = Monday ... 7 = Sunday), `day_name`, `is_weekend`.

Useful for weekday/weekend and month-name questions; for simple period filters, filtering
`invoice_date` directly is simpler and equivalent.
