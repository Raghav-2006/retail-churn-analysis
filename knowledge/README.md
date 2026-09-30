# Knowledge base for the AI analyst

Short markdown docs that the analyst retrieves (pgvector) before it writes SQL. Each doc has YAML
front matter: `id`, `title`, `doc_type`, `owner`, `last_updated`, `deprecated`, and
`superseded_by` for deprecated docs. This README is not indexed.

Doc types: `metric_definition`, `methodology`, `data_caveat`, `table`, `policy`, `faq`.

Four docs are **deliberately outdated or conflicting**, to test that retrieval and the agent
prefer the current definition:
- `metric-churn-2010`, `metric-active-customer-2009`, `metric-aov-legacy`: marked `deprecated: true`;
- `sales-team-faq-2010`: *not* flagged, but older than, and contradicted by, the current metric
  definitions. `policy-definition-precedence` says which doc wins.

The docs are written for this dataset; the numbers in them come from `metrics/*.json`.
