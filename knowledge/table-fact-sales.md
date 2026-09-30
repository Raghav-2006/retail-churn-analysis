---
id: table-fact-sales
title: "Table: fact_sales"
doc_type: table
owner: Data Engineering
last_updated: 2011-12-10
deprecated: false
---
# fact_sales

One row per invoice line of a completed sale (776,579 rows).

| column | type | meaning |
|---|---|---|
| sales_line_id | BIGINT | primary key |
| invoice | TEXT | invoice number; one invoice = one order |
| invoice_date | TIMESTAMP | when the invoice was raised |
| date_key | INTEGER | YYYYMMDD, joins dim_date |
| customer_id | INTEGER | joins dim_customer |
| stock_code | TEXT | joins dim_product |
| country | TEXT | country of the sale |
| quantity | INTEGER | units, always > 0 |
| price | NUMERIC | unit price in GBP, always > 0 |
| revenue | NUMERIC | quantity x price, computed by Postgres |
