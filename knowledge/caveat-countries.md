---
id: caveat-countries
title: Countries and market concentration
doc_type: data_caveat
owner: Data Engineering
last_updated: 2011-12-10
deprecated: false
---
# Countries

- 41 countries; the **United Kingdom is 83.7% of revenue**. Country names are as recorded:
  "EIRE" is Ireland, "RSA" is South Africa, "Unspecified" means unknown.
- Use `fact_sales.country` for the country of a sale. It is almost always the customer's country,
  but a few customers ordered from more than one country.
- See `metric-international-revenue` for how domestic and international revenue are split.
