---
id: table-dim-customer
title: "Table: dim_customer"
doc_type: table
owner: Data Engineering
last_updated: 2011-12-10
deprecated: false
---
# dim_customer

One row per customer ID seen on a sale or a cancellation.

| column | meaning |
|---|---|
| customer_id | primary key |
| country | home country: the country on most of the customer's invoices |
| first_invoice_date | first sales invoice (NULL for customers who only cancelled) |
| last_invoice_date | last sales invoice (NULL for customers who only cancelled) |

The dates are computed over the whole data set, so they equal `MIN/MAX(fact_sales.invoice_date)`
per customer (see `metric-new-customer`). Customers who only ever cancelled have NULL dates.
