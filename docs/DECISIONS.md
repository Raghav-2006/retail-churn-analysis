# Decisions

Every major choice in this project: what was chosen, why, and the alternative that was rejected.
Numbers come from [RESULTS.md](../RESULTS.md).

## v1: pipeline, tiering, AI analyst

| # | Decision | Why | Rejected alternative |
|---|---|---|---|
| 1 | **PostgreSQL via `pgserver`** (embedded Postgres 16), with `DATABASE_URL` to override | Real Postgres with no Docker or system install; CI uses a `postgres:16` service instead | DuckDB (not the target warehouse); SQLite (different SQL dialect) |
| 2 | **Star schema:** 2 fact tables at order-line grain plus 3 conformed dimensions | Answers every question in the brief with simple joins; cancellations stay separate so gross and net revenue are both explicit | One wide table (duplicates attributes, hides grain) |
| 3 | **Idempotent truncate-and-load inside one transaction**, with a content fingerprint per run | A rerun gives identical tables; a crash rolls back to the last good load, so readers never see a half-empty warehouse | Upserts (more code, and they don't remove rows that disappeared from the source) |
| 4 | **Postgres recomputes `revenue` as a generated column** | The revenue reconciliation compares two independent calculations, not a copied value | Load pandas' revenue column (the check could never fail) |
| 5 | **Quality checks FAIL the run** (exit 1); volume anomalies only WARN | A wrong number reaching the tiering model or the analyst is worse than a failed run; the volume check has known false positives (seasonal peaks) | Log-and-continue |
| 6 | **Partial-period detection** by calendar coverage (under 50% of the month) | The row-count z-score missed December 2011 (9 of 31 days) because seasonal peaks inflate the standard deviation | Z-score alone |
| 7 | **Tiering with a time split**: features before 2011-06-01, outcome Jun–Nov 2011 | Validation must use the future, or it measures memorisation | A random train/test split (leaks the future) |
| 8 | **Percentile-ranked features and documented weights** | Weights mean the same thing whatever the units; the score is explainable per feature (`explain()`) | Raw min-max scaling (distorted by outliers such as one £77k order) |
| 9 | **The learned model is trained one period earlier** (Dec 2010 → May 2011) | Judging it on the same outcome it was fit on would be leakage | Fitting on the validation window |
| 10 | **Report that monetary-only beats v1 on top-10% capture** (62.3% vs 58.3%) | Honesty; v2 weights (learned, rounded) are what I would ship | Hiding the weaker headline |
| 11 | **Analyst runs as a read-only role** plus a SELECT-only validator, a statement timeout and a row cap | Defence in depth: the database refuses writes even if the validator is bypassed | Trusting the prompt alone |
| 12 | **Execution accuracy by result-set comparison** | Different SQL can be equally right; string match punishes that and misses plausible wrong numbers | Exact SQL match |
| 13 | **Confidently-wrong rate as the headline metric** | An authoritative wrong number is the costly failure; an abstention is visible | Accuracy alone |
| 14 | **Frozen eval set v2** (SHA-256 pinned in tests) before running v3 | The comparison between versions has to be on identical questions | Editing questions between runs |

## Phase 5: dbt transformation layer

| # | Decision | Why | Rejected alternative |
|---|---|---|---|
| 15 | **The loader's `retail` tables stay as dbt's *source* layer**; dbt builds the analytics layer in `analytics` | The frozen v1 analyst eval (55 reference queries) and the 17 quality checks target `retail.*`; moving them would invalidate every v1 result. The loader already cleans, reconciles and constrains the data, which is what a source layer should guarantee | Load only raw lines and let dbt build the whole star schema (cleaner lineage, but it breaks v1 reproducibility) |
| 16 | **Freeze v1 per-customer outputs before touching code** (`metrics/v1_tiers.csv`, exact `repr` scores) | "Matches v1 exactly" needs a fixed reference; recomputing v1 afterwards would compare new code with itself | Comparing only tier counts (would hide customers swapping tiers) |
| 17 | **Equality with no tolerance**, including scores bit for bit | Any drift is a finding; a tolerance would hide it | Comparing scores with `np.isclose` |
| 18 | **`mart_customer_features` is long: one row per (cutoff date, customer)** | The same mart serves the training cutoff (2010-12-01) and the scoring cutoff (2011-06-01); new cutoffs are one var change | One table per cutoff |
| 19 | **`mart_customer_tiers` re-implements the score in SQL**, and a test asserts it equals Python | Tiers become a queryable, documented table for BI and the analyst. The equality test turns two implementations into a cross-check instead of a divergence risk | Tiers only in Python (not queryable) |
| 20 | **Percentile rank as a macro** reproducing pandas `rank(pct=True, method="average")` in float8 | Tie-averaging, NULL handling and float arithmetic all have to match for bit-exact scores | `percent_rank()` (a different formula: (rank-1)/(n-1)) |
| 21 | **Weights live in `dbt_project.yml` vars, and a test pins them to `score.py`** | One number, two places, kept in sync by CI | Hard-coding weights in SQL |
| 22 | **dbt runs from Python (`pipeline/dbt_runner.py`)**, with the connection derived from the pipeline's DSN | One source of truth for where the warehouse lives (the embedded server's Unix socket, or `DATABASE_URL`); lets the parity check point dbt at other schemas | A hand-maintained `profiles.yml` with credentials |
| 23 | **No dbt packages** (`dbt_utils`); the one generic test needed (`unique_combination`) is written as a local macro | No network dependency on the dbt hub at build time | `dbt_utils.unique_combination_of_columns` |
| 24 | **CI runs a real `dbt build` on synthetic data** (40 customers) | Proves the project compiles and its tests pass on every push, without the 45 MB dataset | Only running dbt locally |

## Phase 6: orchestration and migration parity

| # | Decision | Why | Rejected alternative |
|---|---|---|---|
| 25 | **Airflow 3.1.8, run standalone with `airflow dags test`** and a SQLite metadata DB | It runs in this environment, so the brief's Prefect fallback wasn't needed; `dags test` executes the real DAG with no scheduler or webserver to manage | Prefect (fallback only); a full `airflow standalone` stack (more moving parts for the same evidence) |
| 26 | **Airflow in its own virtualenv**, installed with the official constraints file; tasks run with the project's Python via `BashOperator` | Airflow 3.1 can't use SQLAlchemy 2.1, which the project runs on: an unconstrained install failed on import until Airflow's constraints pinned an older version. Keeping the two environments apart means the pipeline code runs with exactly v1's dependencies | `PythonOperator` in a shared environment (dependency conflicts); `ExternalPythonOperator` (same isolation, less transparent) |
| 27 | **Each task is a small CLI step (`python -m pipeline.steps <task>`) that reuses the v1 functions**, with handoff through files in `data/runs/<run>/` | Tasks can be retried and inspected on their own; the orchestrated job is the same code as the legacy one, so parity measures orchestration, not a rewrite | Passing DataFrames through XCom (not meant for large data); rewriting logic inside the DAG |
| 28 | **No retries, `max_active_runs=1`** | Data failures aren't transient; two concurrent runs would truncate each other's tables | Default retries (would hide and delay data-quality failures) |
| 29 | **The legacy job is the unchanged v1 script** (with only a `--schema` option added) **plus v1 tiering** | Parity must be measured against what production actually ran, not a re-implementation | Comparing against a snapshot (can't take new data) |
| 30 | **Parity compares column checksums, not just row counts** (md5 of every column in key order) | Row counts miss changed values; checksums catch any value change and name the affected column | Row counts and totals only |
| 31 | **Parity also compares every customer's tier** | The business output is the tier; a pipeline can match on totals and still re-tier customers | Aggregate tier counts only |
| 32 | **A per-country drill-down in the parity report** | An alert should say where to look ("Norway 1,263 → 0"), not just "mismatch" | A bare pass/fail |
| 33 | **Inject a *silent* bug (drop one country) and a *loud* one (negative prices)** | Shows both paths: internal checks catch what they can see, and parity catches what they can't | Only demonstrating a failure the quality checks would catch anyway |
| 34 | **Alerts as JSON lines (`logs/alerts.jsonl`) and the task fails** | Machine-readable (source, check, run_id, details), so it's easy to route to Slack or PagerDuty later; failing the task stops downstream work | Log messages and continuing; email from inside the task |
| 35 | **Zero LLM calls in Phases 5–6:** every command ran with `GEMINI_API_KEY` unset, and DAG tasks override it to empty | The API credits are depleted, and none of these steps need an LLM; unsetting the key makes that guarantee mechanical | Relying on not calling the analyst |
