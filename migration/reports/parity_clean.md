# Parity report: clean

- Injected bug: `none`
- Legacy job exit code 0; orchestrated Airflow run: **success** (extract success, transform success, load success, quality success, dbt_build success, tiering success, export success)
- The orchestrated run's own checks: 17 quality checks, 0 failed; dbt tests {'pass': 63}
- **Parity: 100.0%** (46 of 46 checks match); verdict: **PASS**
- Tiers: 100.0% of customers agree; 0 missing, 0 changed tier

| check | matching | total |
|---|---|---|
| row_count | 5 | 5 |
| column_checksum | 38 | 38 |
| revenue_total | 2 | 2 |
| tier assignments (customers) | 4908 | 4908 |
