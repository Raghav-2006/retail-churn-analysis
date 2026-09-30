# Interview notes

Likely questions, answered from what this repo actually did and measured. Every number is in
[RESULTS.md](../RESULTS.md); the reasoning behind each choice is in [DECISIONS.md](DECISIONS.md).

## The 30-second version

> I built a retail sales platform on 1M+ real transactions. An Airflow-orchestrated pipeline loads
> PostgreSQL through dbt, with 17 quality checks and 63 dbt tests. When I migrated it, a
> parallel-run parity check caught a bug that had passed every one of those checks. A multi-factor
> customer tiering model is validated on the next six months' revenue, and it's honest that a
> simpler baseline wins on one metric. A Gemini text-to-SQL analyst is measured on how often it is
> confidently wrong: 3.4% down to 0.2% across three models. It's all served through FastAPI and a
> Streamlit dashboard.

---

## Pipeline and data quality

**Why a star schema?**
The questions are "revenue by X over time": two fact tables at order-line grain (sales,
cancellations) with conformed customer, product and date dimensions make each one a simple
join. Keeping cancellations separate makes gross vs net revenue explicit, which turned out to be
the analyst's biggest source of confusion.

**What makes the load idempotent, and why does it matter?**
Truncate-and-load of all five tables inside one transaction. A rerun gives identical tables
(proved by an md5 fingerprint of every table's content, logged per run), and a crash rolls back to
the last good load. It matters because orchestrators retry and people rerun jobs; a non-idempotent
load double-counts on the second run.

**How do you know the load is correct?**
17 checks run on every load and fail the run:
- row counts per table;
- revenue reconciled to the penny (£17,068,582.72), where Postgres recomputes revenue itself as a
  generated column, so it's an independent calculation;
- nulls, keys and referential integrity;
- a schema-drift contract;
- freshness;
- a volume check.

Result: 16 pass, 1 warns, 0 fail.

**Tell me about a check that didn't work.**
The volume check first used only a rolling z-score. It flagged the Sep–Nov seasonal peaks (false
positives) but **missed December 2011**, which has only 9 of 31 days, because the peaks inflate
the standard deviation. I added a partial-period test based on calendar coverage (under 50% of the
month). It's a WARN, not a FAIL, because of the seasonal false positives; a year-over-year
baseline is the real fix.

## dbt

**Why dbt on top of a loader that already builds a star schema?**
dbt owns the analytics layer: staging views that rename and type columns, and marts
(`mart_customer_features`, `mart_customer_tiers`) with 63 tests, lineage and docs. I kept the
loader's tables as dbt's *source* on purpose: the frozen v1 analyst eval and the quality checks
query them, so moving them would have invalidated v1's results.

**How did you prove the migration changed nothing?**
Before touching any code I froze v1's per-customer scores and tiers (`metrics/v1_tiers.csv`, exact
float `repr`). After the move:
- the mart features equal v1's SQL (73,176 values);
- the Python tiers from the mart equal v1 for all 4,908 customers, **scores bit for bit**;
- the SQL tier mart equals v1 too.

No tolerance anywhere, because a tolerance hides drift.

**How can SQL and Python produce bit-identical floats?**
The percentile rank is a macro that reproduces pandas' `rank(pct=True, method="average")`: average
rank for ties, NULLs ranked last, a denominator that counts only non-NULLs, float8 arithmetic.
The weighted sum adds terms in the same order. `percent_rank()` would have been wrong, because it
computes (rank-1)/(n-1).

## Orchestration and migration

**Walk me through the DAG.**
`extract → transform → load → quality → dbt_build → tiering → export` (Airflow 3.1.8). Each task
is `python -m pipeline.steps <task>`, reusing the v1 functions, with files as the handoff. There
are no retries, because data errors aren't transient, and `max_active_runs=1`, because two runs
would truncate each other's tables. Any failure writes a structured alert to
`logs/alerts.jsonl` and fails the task, so everything downstream is skipped.

**Which check would catch a silent failure after a migration?**
None of the internal ones: I proved that. I injected a transform bug that drops Norway's 1,263
rows. The Airflow run finished **success**, with **0 of 17 quality checks failing and 63 of 63 dbt
tests passing**, because every check compares the warehouse with the run's *own* transform
output. The **parity check** caught it: I ran the legacy job and the DAG into separate schemas and
compared row counts, 38 column checksums, revenue and every customer's tier. Parity was 58.7%, and
the country drill-down said "Norway 1,263 → 0". The clean run scored 100%.

**How would you cut over in production?**
Run both in parallel for a period. Require 100% parity on every run, not once. Alert on any
mismatch. Only then decommission the legacy job. At scale I'd compare per-partition checksums
rather than full tables.

**Why Airflow in its own virtualenv?**
Airflow 3.1 can't use SQLAlchemy 2.1, which the project uses; an unconstrained install failed on
import. Its official constraints file plus a separate venv keeps the pipeline on exactly v1's
dependencies, and the tasks call the project's Python.

## Customer tiering

**How did you pick the weights?**
I started from documented weights (recency 0.25, frequency 0.25, monetary 0.30, breadth 0.10,
cancellations 0.10) applied to percentile-ranked features, so a weight means the same thing
whatever the units.

**How do you know the tiers are any good?**
Time-split validation. Customers were scored on 1 Jun 2011 using only earlier data, then judged
on Jun–Nov 2011 revenue:
- mean future revenue falls monotonically by tier: £5,073 → £1,007 → £368 → £125;
- Tier 1 (the top 10%) earned 58.3% of that revenue;
- Spearman correlation with future revenue is 0.607, the best of all methods.

**Did it beat the baselines?**
Partly, and I say so. It ranks the whole base best, but for the top 10%, **ranking by past revenue
alone captured more: 62.3%**. A logistic regression trained one period earlier (so no leakage)
moved weight toward monetary and average order value. Rounded into v2 weights, it captured 62.6%,
only 0.3 points above monetary-only.

**Which would you ship to sales stakeholders?**
v2. It's still a five-number weighted sum anyone can audit, it matches the learned model, and
`explain(customer_id)` shows the points each input adds.

**Explain a customer's tier to a sales manager.**
Customer 12346 placed the biggest single order in the data, £77k, and then cancelled it. With
only 3 orders and none for 4 months, they sit in Tier 3 despite that order. They spent £0 in the
next six months. That's the case where monetary-only ranking is fooled and the multi-factor score
isn't.

**What is data leakage, and how did you prevent it?**
Features see only transactions strictly before the cutoff; the outcome window starts at the
cutoff. The learned model was trained on the previous period (Dec 2010 → May 2011) and applied
unchanged. Tuning weights on the validation outcome would have been leakage.

**How sensitive are the tiers to the weights?**
Changing any single weight by ±50% moves at most 8.3% of customers to a different tier, and
top-10% capture stays between 57.5% and 59.4%.

## AI analyst and evaluation

**How do you evaluate a text-to-SQL agent?**
- 55 hand-labelled questions (12 of them unanswerable), each answerable one with a reference SQL.
- **Execution accuracy** compares *result sets*: order-insensitive, numeric tolerance, extra
  columns allowed. It doesn't compare SQL text, because two different queries can both be right,
  and a plausible query can return the wrong number.
- The eval set is frozen (SHA-256 pinned in a test), so every model and prompt version was graded
  on the same file.

**Why "confidently wrong" as the headline metric?**
An abstention or a SQL error is visible. A wrong number delivered with confidence is what costs
money. The rate counts answered-and-wrong plus answered-an-unanswerable, over all questions.

**What were the failure modes?**
I hand-audited every "wrong" verdict. There were three kinds:
- **Definition drift:** gemini-3.5-flash computed 2010 revenue net of cancellations, £7.74M against
  the official £8.22M, in 3 of 3 runs.
- **Fabricated proxies:** a model invented a "profit margin" formula instead of abstaining.
- **Fan-out joins:** joining sales to cancellations on customer or date multiplied rows, giving
  £31.4M "net revenue" for one customer (the true top is £570K), or negative £356M for 2011.

**What mitigations helped, and by how much?**
- **v2** added a business glossary (the company's metric definitions) and a no-proxy rule.
- **v3** added a self-check: the model reviews its SQL and a result preview for fan-out, filtering
  before window functions, units and plausibility.
- Pooled over 3 models, confidently wrong went **3.4% → 1.6% → 0.2%** (17/495 → 8/495 → 1/440).
- **Honest caveats:** the self-check rewrote SQL in only 2 of 165 runs on one model, so part of
  that model's gain is run-to-run variance. On another model it fixed 12 runs, all correctly. The
  mitigations were designed on the same set, so gains may be optimistic.

**What surprised you?**
The first eval set was saturated (100%). Two "failures" were grader bugs: a month returned as
`2011-01`, and a share returned as a fraction rather than a percentage. The grader needed as much
testing as the model.

**How do you detect a confidently wrong answer when there's no reference?**
In production you don't have a reference. You use:
- the self-check (the model reviews its SQL and a result preview);
- plausibility bounds;
- reconciliation against known totals;
- abstention when a concept has no column.

The eval is what tells you how often those signals miss.

## Serving

**How does the demo work without credits or a database?**
A 1.68 MB pre-aggregated SQLite database is committed to the repo. It holds the KPIs, all 4,908
scored customers (the same `explain()` runs on it) and the 495 cached, graded eval answers.
- **FastAPI** serves `/health`, `/customers/{id}/tier` and `/ask`.
- An uncached question gets a 404: "no answer rather than a guess".
- **Live mode** has a rate limit and a timeout that returns an explicit error.
- **Docker:** the image builds, runs and passes its healthcheck; compose adds Postgres 16.
- **Streamlit:** was run in a clean environment with only the dashboard requirements, without
  importing any database or LLM libraries.

**What would you do next?**
- Use a year-over-year baseline for the volume check.
- Backtest the tiers over rolling cutoffs to get confidence intervals.
- Hold out a question set for the analyst.
- Add retrieval of business definitions (RAG) instead of a fixed glossary.
- Route alerts to Slack.
