# Parity report: injected

- Injected bug: `drop_country:Norway`
- Legacy job exit code 0; orchestrated Airflow run: **success** (extract success, transform success, load success, quality success, dbt_build success, tiering success, export success)
- The orchestrated run's own checks: 17 quality checks, 0 failed; dbt tests {'pass': 63}
- **Parity: 58.7%** (27 of 46 checks match); verdict: **FAIL**
- Tiers: 99.878% of customers agree; 5 missing, 1 changed tier

| check | matching | total |
|---|---|---|
| row_count | 3 | 5 |
| column_checksum | 23 | 38 |
| revenue_total | 1 | 2 |
| tier assignments (customers) | 4902 | 4908 |

**Mismatches**

| kind | name | legacy | orchestrated |
|---|---|---|---|
| row_count | dim_customer | 5928 | 5919 |
| row_count | fact_sales | 776579 | 775316 |
| column_checksum | dim_customer.country | 3cb7fdb557992bcae9 | fe39aac008671c194f |
| column_checksum | dim_customer.customer_id | 91a39210412d6927f4 | bd7b26bda198942e3c |
| column_checksum | dim_customer.first_invoice_date | 4d0530903429f2f562 | 67012adf08d227689c |
| column_checksum | dim_customer.last_invoice_date | ad4521bf914db2cf39 | 6049caa0362d36a6eb |
| column_checksum | dim_product.description | 3095a29e1568e2a164 | 6ae7c065036284cc59 |
| column_checksum | fact_sales.country | 18e8a06a0e3ec785b5 | cc43d2831a32a518f2 |
| column_checksum | fact_sales.customer_id | 3c6291be1cb42b32f2 | 3588f8a530e2c699be |
| column_checksum | fact_sales.date_key | 78c093eb489153a457 | a769f63a75aec6b375 |
| column_checksum | fact_sales.invoice | 7140946a007a08d7e3 | 4ecaf41cb1f35f0b4a |
| column_checksum | fact_sales.invoice_date | caea1be17d182c3f3c | 69c71dd15589a0131e |
| column_checksum | fact_sales.price | 7e0069a9307d24e0a5 | 9009951bbb6f30bede |
| column_checksum | fact_sales.quantity | 16bf7fddc46a9d0947 | dca33c3e102b788c4e |
| column_checksum | fact_sales.revenue | 6dbc06b2eaeb10c67d | 1a3b8f1f16b9c332ce |
| column_checksum | fact_sales.sales_line_id | 339fcf8e4c69f048e7 | 5a534f766ffe9d2866 |
| column_checksum | fact_sales.stock_code | f5a1f9fc243fc40b58 | 1e68dabfee5ceac48a |
| revenue_total | fact_sales | 17068582.72 | 17030007.36 |

**Row counts that differ, by country**

| country | legacy | orchestrated |
|---|---|---|
| Norway | 1,263 | 0 |

**Tier transitions (legacy -> orchestrated)**

- Tier 3 -> missing: 2
- Tier 4 -> missing: 2
- Tier 2 -> Tier 3: 1
- Tier 2 -> missing: 1
