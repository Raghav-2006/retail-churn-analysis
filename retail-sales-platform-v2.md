# Build brief v2: Retail Sales Platform — production and AI upgrades

Run this **after** the v1 spec (retail-sales-platform-spec.md) is complete. Same rules: every number comes from code that ran, commit after each phase, be honest about weak results. Each phase maps to a line in Apple's *Data Science & Analytics – Student Position (Sales)* posting, noted in the **JD:** line.

Order matters. Do phases in sequence, and stop and report after each one so Raghav can review before the next.

---

## Phase 5 — dbt transformation layer
**JD:** "PostgreSQL, Snowflake, dbt"; "Optimize … warehouse models"

1. Add `dbt-postgres`. Move the star schema and analytical marts into dbt models: `staging/` (typed, renamed raw), `marts/` (`fct_sales`, `dim_customer`, `dim_product`, `mart_customer_features`, `mart_customer_tiers`).
2. dbt tests: `unique`, `not_null`, `relationships`, `accepted_values`, plus 2 custom tests (revenue reconciliation; no future-dated invoices).
3. Generate dbt docs, and commit a screenshot of the lineage graph to `figures/`.
4. The tiering code reads from `mart_customer_features` instead of raw tables.

**Verify:** `dbt build` passes from scratch. The tier outputs match v1 exactly (assert equality).

## Phase 6 — Orchestration + migration parity
**JD:** "Migrate scheduled jobs … into Apache Airflow … avoiding silent failures"; "validation and parallel-run strategies to confirm parity before decommissioning"

1. Orchestrate the full pipeline with **Airflow** (standalone/SequentialExecutor with SQLite metadata is fine). If Airflow can't run in this environment, use **Prefect** and say why in the README. The DAG runs extract → transform → load → quality checks → dbt build → tiering → export.
2. Treat the v1 `python -m pipeline.run` script as the "legacy job." Write `migration/parity_check.py`, which runs legacy and orchestrated versions into separate schemas and compares row counts, column checksums, revenue totals and tier assignments per customer. Output a parity report.
3. **Inject a silent failure** (e.g. a transform bug that drops one country) and show that the parity check catches it. Document this in the README as the headline of this section.
4. Alerting: on any quality-check or parity failure, write a structured alert to `logs/alerts.jsonl` and fail the task (don't continue silently).

**Verify:** a clean run gives parity = 100%, and the injected bug gets detected.

## Phase 7 — RAG knowledge layer for the AI analyst
**JD:** "RAG systems … against internal knowledge sources"; "knowledge base design, content structuring, retrieval quality"; "Vector search: pgvector"

The v1 analyst only sees the schema. Real analysts need **business definitions**. Build a small knowledge base:

1. Write `knowledge/` as ~25–40 short markdown docs: metric definitions (active customer, churn, AOV, cancellation rate, net vs gross revenue), tier methodology, data caveats (Dec 2011 partial, missing customer IDs, GBP), table and column descriptions, and 3–4 deliberately **conflicting or outdated** docs (e.g. an old churn definition marked deprecated).
2. Chunk the docs, embed them (Gemini embedding model), and store them in **pgvector**, with metadata (doc type, last_updated, deprecated flag).
3. Before writing SQL, the agent retrieves the top-k chunks and must **cite** which definitions it used. It prefers non-deprecated docs.
4. **Retrieval eval:** for each eval question, label which doc(s) are relevant. Report recall@k and MRR, and compare k = 3/5/8 and chunk sizes.
5. Add 10 eval questions that **require** a definition to answer correctly (e.g. "How many active customers in Q3 2011?").
6. **Ablation:** run the full eval with no RAG vs with RAG. Report execution accuracy and confidently-wrong rate for both.

**Verify:** the results table shows the with/without RAG comparison, whatever the outcome.

## Phase 8 — Stronger evaluation: LLM-as-judge + human agreement
**JD:** "benchmark suites"; "LLM-as-judge methods and hallucination detection"; "methods for detecting confidently incorrect answers"

1. Add an LLM-as-judge that scores each natural-language answer for **faithfulness** (is it supported by the SQL result?) and **citation correctness**.
2. **Calibrate the judge:** Raghav hand-labels 30 answers (produce a CSV for him to fill in). Report judge–human agreement (accuracy and Cohen's kappa). If agreement is poor, say so and don't rely on the judge.
3. Add a **self-verification step** in the agent: after answering, it re-checks that the numbers in its answer appear in the SQL result. If not, it abstains or corrects. Measure the effect on the confidently-wrong rate.
4. Final eval table: per-version rows (baseline → +few-shot → +RAG → +self-check) × (execution accuracy, abstention accuracy, confidently-wrong rate, p50/p95 latency, avg cost per question).

## Phase 9 — Serve it: API + live demo
**JD:** "FastAPI, Docker, and asynchronous Python"; "structured logging, caching, and fallback mechanisms"; "reporting and visualization that makes model results usable"

1. **FastAPI** (async) endpoints: `POST /ask`, `GET /customers/{id}/tier` (with explain breakdown), `GET /health`. Add structured logging, a response cache, and a **fallback** (if the LLM times out or errors, return a clear error and do not guess).
2. **Dockerfile** plus `docker-compose.yml` (API + Postgres). If Docker can't run here, write and lint the files anyway and note that they're untested.
3. **Streamlit dashboard** with three tabs:
   - Overview KPIs and charts.
   - **Tier Explorer:** pick a customer and see their tier and feature-contribution breakdown.
   - **Ask the Data:** a question box showing the answer, the SQL, cited definitions, and a confidence/abstain flag.
4. Deploy the dashboard to **Streamlit Community Cloud** from the repo, using a small **pre-aggregated demo database** (SQLite or parquet committed to the repo, under 50MB) so it runs without the full pipeline. The Ask tab uses the Gemini key from Streamlit secrets, is rate-limited, and has a "demo mode" of cached example questions if the key is missing.
5. Put the live demo link at the top of the README.

## Phase 10 — Polish
1. The README becomes a one-screen summary: live demo link, architecture diagram (mermaid), a key-results table (pipeline quality, tiering validation, AI eval), then details.
2. Code quality: `ruff` lint, type hints on public functions, and CI running lint + pytest + dbt tests.
3. Write `docs/DECISIONS.md` (every major decision, why, and the rejected alternative) and `docs/INTERVIEW_NOTES.md` (likely interview questions with answers grounded in this repo's actual results).

---

## Final report to Raghav
All numbers from RESULTS.md, the live demo URL, and anything that didn't work or was skipped, and why.

## Resume bullets (fill in real numbers only)

> **Retail Sales Data Platform** — [Live Demo] · [GitHub] | *Python, PostgreSQL, dbt, Airflow, pgvector, FastAPI, Docker, Gemini API, scikit-learn*
> - Built an orchestrated ELT pipeline (Airflow, dbt) loading 1M+ transactions into a PostgreSQL star schema with automated quality checks and a parallel-run parity harness that caught an injected silent failure before cutover.
> - Designed a multi-factor customer tiering model validated on future revenue (Tier 1 captured X% vs Y% for a monetary-only baseline), with per-customer explanations served via FastAPI and a Streamlit dashboard.
> - Built a RAG-grounded text-to-SQL analyst (pgvector) evaluated on a N-question benchmark; cut the confidently-wrong rate from A% to B% via retrieval and self-verification, with an LLM judge calibrated against human labels (κ = K).
