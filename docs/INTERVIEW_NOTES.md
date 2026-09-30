# Interview notes

Likely questions, answered from what this repo actually did and measured. Every number is in
[RESULTS.md](../RESULTS.md); the reasoning behind each choice is in [DECISIONS.md](DECISIONS.md).

## The 30-second version

> I built a retail sales platform on 1M+ real transactions. An Airflow-orchestrated pipeline loads
> PostgreSQL through dbt, with 17 quality checks and 63 dbt tests. When I migrated it, a
> parallel-run parity check caught a bug that had passed every one of those checks. A multi-factor
> customer tiering model is validated on the next six months' revenue, and it's honest that a
> simpler baseline wins on one metric. A Gemini text-to-SQL analyst is measured on how often it is
> confidently wrong: 3.4% down to 0.2% across three models. Adding a pgvector knowledge base
> of business definitions took questions that need a definition from 20% to 100% correct, and cut
> the confidently-wrong rate on 65 questions from 9.2% to 1.5%, for under CA$0.40 of API spend.
> It's all served through FastAPI and a Streamlit dashboard.

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

## RAG knowledge layer, LLM judge and self-verification

**Why add RAG to a text-to-SQL agent that already scored 100%?**
Because the 55 questions only used definitions that were in the schema or the glossary. Real
questions depend on business definitions that live in documents: "active customer", "churn",
"AOV". I wrote 10 questions that need one, as a separate frozen set. Without retrieval,
gemini-3.1-flash-lite got **2 of 10 right** (20%) and was **confidently wrong on 60%** of runs:
- gross AOV £480.48 instead of the official net £445.60;
- its own churn window (4,026 churned instead of 3,050);
- a different peak season.

With the knowledge base it got **10/10 in both runs**. Over all 65 questions, confidently wrong
went **9.2% → 1.5%**.

**What did RAG break?**
One frozen question, m04: "average order value, where an order's value is the total revenue of its
invoice". The net-revenue AOV doc overrode the definition *stated in the question*, in both runs,
even though my precedence policy says the question wins. Retrieval gives the model a stronger
prior than the question text. The fix would be to detect "where X is defined as…" and suppress
conflicting definitions, not to reword the doc.

**How did you evaluate retrieval separately from generation?**
- I hand-labelled the relevant docs for all 65 questions.
- I report recall@k, hit@k and MRR at doc level, over the top-k chunks the agent actually sees.
- I compared chunk sizes of 32/64/128 words and whole docs, at k = 3/5/8.
- Whole docs at k = 8: recall **0.94**, MRR **0.79** on answerable questions.
- The setting was picked by a rule fixed before looking: best recall@8, then the smallest k within
  0.02. So it was not picked by end-to-end accuracy, which would tune on the test set.

**Where does retrieval fail?**
Unanswerable questions: recall ≈ 0. The doc that says "there is no cost, salesperson or marketing
data" doesn't embed near "what was our profit margin?". Negative knowledge is hard to retrieve.
The agent still refused all 12 from the schema alone, so it didn't cost accuracy here. In
production I'd give each out-of-scope topic its own doc, or add a keyword rule.

**How did you handle outdated or conflicting docs?**
- 3 docs are marked deprecated with `superseded_by`, and one older FAQ contradicts the metric
  definitions without being flagged.
- Deprecated chunks get a similarity penalty and a "DEPRECATED, superseded by …" label. With the
  penalty, no deprecated doc outranked the current definition. Without it, one did for up to 20%
  of definition questions.
- No answer cited a deprecated doc. The FAQ is the harder case: it reaches the prompt for 30% of
  definition questions even with deprecated docs excluded, and a written precedence policy
  (metric definition beats FAQ, newer beats older) is what resolves it.

**Why local embeddings and pgvector?**
- 34 short docs: MiniLM on CPU is free, deterministic and offline. The API budget went to the
  generations that matter.
- pgvector keeps vectors next to the metadata in the Postgres that already exists.
- Search is exact: at a few hundred rows an ANN index can only lose recall.
- CI tests retrieval against real pgvector with a hashed bag-of-words embedder, so it needs no
  torch.

**How do you know the LLM judge is any good?**
I don't yet, and I say so. The judge (same model, temperature 0) scored the v4 answers
**94.3% faithful** and **67.9% citations correct**. But a judge is only a measurement once it
agrees with a human.
- I exported **30 blind answers**, stratified by signals that don't come from the judge.
- The judge–human accuracy and Cohen's kappa are **pending my labels**. If kappa comes out under
  about 0.4, the rule is to say so and not rely on the judge.
- Two things already make me cautious: it grades its own model, and it is strict about
  over-citing.

**What did self-verification do?**
It checks that every number in the prose answer appears in the SQL result (or is a SQL constant,
the row count or in the question). On a mismatch it rewrites once, then abstains.
- On the real answers: **0 of 106 flagged**, so confidently wrong stayed at 1.5%.
- The two remaining errors are faithful restatements of a *wrong* query, which a number check
  can't see.
- To show the step works, I corrupted the headline number in 51 answers. It caught **51/51** and
  rewrote all 51 back to the right number.

It's a cheap guard (no call unless there's a mismatch) against narration errors this model didn't
make here. It is not a fix for wrong SQL: that is what the SQL self-check and RAG are for.

**How did you keep the cost under control?**
Every Gemini call goes through a spend guard that records its tokens in a committed ledger. Each
batch prints its estimated calls and cost before it runs, and a call that could take the total
past CA$8 is refused, keeping completed results.
- Phases 7–8 took **491 calls, about CA$0.39**, against a CA$1.20 plan.
- I reused the cached v3 runs as the no-RAG baseline, ran 2 runs instead of 3, cached judge
  verdicts, and measured v5 by replaying the recorded v4 answers.

## Serving

**How does the demo work without credits or a database?**
A 1.68 MB pre-aggregated SQLite database is committed to the repo. It holds the KPIs, all 4,908
scored customers (the same `explain()` runs on it) and 635 cached, graded eval answers (including the RAG + self-verification answers with their citations).
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
