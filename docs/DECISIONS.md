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

## Phase 9: serving (API, dashboard, Docker)

| # | Decision | Why | Rejected alternative |
|---|---|---|---|
| 36 | **A committed, pre-aggregated SQLite demo database** (1.68 MB) behind both the API and the dashboard | Streamlit Community Cloud has no Postgres, no 45 MB dataset and no pipeline; SQLite ships in the repo and in the image, and it's read-only at runtime | Hosting Postgres for a demo (cost, credentials, a moving part); parquet files (fine, but SQLite gives indexes and one file) |
| 37 | **The demo DB stores `score_customers()` output, so the real `explain()` runs on it** | One code path for explanations in the notebook, API and dashboard; no duplicated logic | Pre-rendering explanation text into the DB |
| 38 | **`/ask` serves the cached eval answers, with their correctness labels** | There are no API credits and, more importantly, those answers are *graded*: the demo can show a confidently-wrong answer next to the reference, which a live call can't | Calling Gemini live by default |
| 39 | **Uncached question → 404 "no answer rather than a guess"**, with the closest cached questions | The whole project is about not being confidently wrong; the fallback must follow the same rule | Returning the nearest cached answer (a guess dressed as an answer) |
| 40 | **Answer text rendered from the SQL result by a template**, and labelled as such | The eval runs didn't narrate (to save calls); inventing prose would be fabrication | Leaving the answer blank |
| 41 | **Live mode needs *both* a key and a warehouse** (`GEMINI_API_KEY` + `DATABASE_URL`), with a hard rate limit (5 per session, 30 per hour; the API allows 10 per hour) and a timeout that returns an explicit error | The analyst must execute SQL somewhere; on Cloud the demo DB has no fact tables. The limits cap cost on a public app | A key-only live mode (it would fail on every question) |
| 42 | **In-process TTL cache and JSON-line access logs** | Single-process service; the cache and logs need no infrastructure, and the logs are machine-readable (request id, latency, cache hit) | Redis or a log shipper (overkill here) |
| 43 | **`tiering.features` imports the database layer lazily** | Lets the dashboard import `tiering.score.explain` with pandas only, so the Cloud build is light (verified: psycopg, SQLAlchemy, dbt and genai are never imported) | Duplicating `explain()` in the dashboard |
| 44 | **`dashboard/requirements.txt` separate from the root requirements** | Cloud needs about 8 packages, not dbt, pgserver or jupyter | One heavy requirements file for everything |
| 45 | **Docker: slim base, dependency layer first, non-root numeric user, HEALTHCHECK, an optional CA build secret**; compose adds Postgres 16 with a healthcheck | Small, cache-friendly, safe defaults; the optional secret lets it build behind a TLS-inspecting proxy without changing the file | Baking the proxy CA into the image |
| 46 | **Chart colours from the validated project palette**, one axis per chart, legends plus data tables | The dataviz check passes; aqua is under 3:1 contrast, so every chart has labels and a table view | Streamlit's default colours |

## Phase 10: polish

| # | Decision | Why | Rejected alternative |
|---|---|---|---|
| 47 | **ruff with pycodestyle, pyflakes, isort, bugbear, pyupgrade and ANN (annotations)**; tests and the DAG file are exempt from ANN | ANN turns "type hints on public functions" from a convention into a CI check | mypy (a larger adoption cost for pandas-heavy code); annotations without enforcement |
| 48 | **Lint only, no `ruff format` sweep** | Reformatting 40 files would bury the real changes in a huge diff and make history hard to review; formatting can be its own PR | Reformatting everything in the same PR |
| 49 | **B905 (`zip` without `strict=`) ignored, with the reason in the config** | Every flagged `zip` is either intentionally uneven (the DAG pairs `tasks` with `tasks[1:]`) or already length-checked | Adding `strict=False` everywhere just to silence it |
| 50 | **CI split into a lint job and a test job, with an explicit dbt step** | A failing check says immediately whether it's style, the dbt project or the Python tests | One monolithic step |
| 51 | **README: one-screen summary (diagram + key-results table), details below** | The architecture and headline numbers fit in the first 30 lines; the detail is below for anyone who wants it | A long narrative README |

## Phase 7: RAG knowledge layer

| # | Decision | Why | Rejected alternative |
|---|---|---|---|
| 52 | **A hard spend guard** (`analyst/budget.py`): every Gemini call's tokens go into a committed ledger; a call that could pass CA$8 is refused before it is made; each batch prints its estimate first | The prepaid credit (≈CA$11) must not run out mid-eval (that already happened once, in Part 3, with a 402). Stopping cleanly keeps the completed results | Checking the balance by hand; trusting a pre-run estimate alone |
| 53 | **One model, gemini-3.1-flash-lite, for the agent and the judge**; the guard refuses unpriced models | A Flash-Lite model, priced for the budget (US$0.25 / 1.50 per 1M tokens), that already scored 100% with v3 on the frozen 55, so any change is due to RAG, not the model | Comparing 3 models again (3× the calls); a stronger judge model (outside the budget) |
| 54 | **Local embeddings** (`all-MiniLM-L6-v2`, 384-d, CPU) instead of the Gemini embedding API | Free, deterministic and offline; 34 short docs don't need a large model. CI uses a hashed bag-of-words embedder, so it needs no torch and no download | Gemini embeddings (cost, network, a second API in the budget) |
| 55 | **pgvector in the same Postgres**, one table with a `config` column for every chunking setting, **exact** cosine search | No new service; metadata (type, `last_updated`, deprecated) sits next to the vector; a few hundred rows make an ANN index pointless (it can only lose recall) | A separate vector DB; HNSW/IVFFlat |
| 56 | **Paragraph-aware chunks prefixed with the doc title**; chunk size chosen by the retrieval eval | A chunk that says "the 90-day window" must still say *what* it defines. Sizes 32/64/128 words and whole doc were compared; whole doc at k = 8 won (docs average 88 words) | Fixed-size token windows with no title |
| 57 | **Deprecated docs penalised (−0.10 similarity) and labelled, not deleted** from the index | Realistic: old docs linger in wikis. The penalty kept deprecated docs below the current definition for 100% of definition questions; labelling tells the model which one wins | Excluding them (hides the failure mode being tested); ignoring the flag |
| 58 | **One unflagged conflicting doc** (a 2010 sales FAQ) plus a written precedence policy | Deprecation flags don't catch everything; the agent has to resolve a conflict between a current metric doc and an old FAQ | Only flagged conflicts (too easy) |
| 59 | **Knowledge docs consistent with the frozen 55's conventions**: "active *in* a period" = bought in it (x10, h16), "active *as of* a date" = trailing 90 days | Otherwise RAG would have contradicted two frozen references and the ablation would measure my inconsistency, not RAG | Changing the frozen eval set (never allowed); one definition only |
| 60 | **10 definition questions as a separate frozen file** with its own pinned SHA; the 55 untouched | The existing results stay comparable; the new questions test exactly what RAG adds (definitions not in the schema or glossary) | Adding them to `eval_set.yaml` (would invalidate every earlier number) |
| 61 | **Relevance labels at doc level, over the top-k chunks the agent actually sees** | Recall@k then means "was the definition in the prompt", which is what matters for the answer | Chunk-level labels (tied to one chunking setting) |
| 62 | **k and chunk size chosen by a rule fixed before the results**: best recall@8, then the smallest k within 0.02 | Avoids picking the setting that flatters the ablation | Picking by eyeballing, or by end-to-end accuracy (would tune on the test set) |
| 63 | **No-RAG baseline on the 55 = the cached v3 runs 0–1**, SHA-checked; only the 10 new questions ran fresh for v3 | Same question file, prompt and model: re-running would spend budget to reproduce known numbers | Re-running v3 on all 65 |
| 64 | **Report the m04 regression and the unanswerable-retrieval miss as they are** | RAG overrode a definition stated in the question; the "what the data doesn't contain" doc matches almost nothing. Both are real findings | Rewording docs until the eval looked clean (tuning on the test set) |

## Phase 8: LLM judge and self-verification

| # | Decision | Why | Rejected alternative |
|---|---|---|---|
| 65 | **Judge sees the question, SQL, result, answer and every shown doc, never the grade** | Faithfulness ("supported by the result") and correctness ("right query") are different failures; mixing them would make the judge a second grader | Letting the judge see the reference answer |
| 66 | **Judge verdicts cached by a hash of their exact input** | Identical answers across runs cost one call (53 calls for 106 answers); re-runs are free and reproducible | Re-judging every run |
| 67 | **30 blind, stratified answers for hand labels**, chosen by signals that don't come from the judge (definition questions, wrong answers, number-check flags, then random) | Agreement measured only on easy, all-correct answers would inflate kappa; the judge's own verdicts must not pick the sample | Random 30 (almost all "faithful"); sampling on judge disagreements (biased) |
| 68 | **Agreement is reported as "pending human labels"**, and the judge's verdicts are shown as uncalibrated hints until then | An uncalibrated judge is not a measurement; no labels were guessed or generated | Reporting judge scores as if calibrated; auto-labelling |
| 69 | **Self-verification is deterministic**: numbers in the answer vs result cells, SQL constants, the row count and the question; an LLM call only to rewrite on a mismatch, then abstain | Cheap (0 calls when the answer is clean) and not fooled by the model agreeing with itself | A second LLM pass on every answer (doubles cost, same blind spots) |
| 70 | **Constants written in the executed SQL count as supported** | The first version flagged "90 days" and "£1,000" in 8 correct definition answers; those numbers come from the definition, not the result | Allow-listing specific numbers |
| 71 | **v5 measured by replaying the recorded v4 answers through the step**, plus **fault injection** | v5 changes nothing before the answer is written and v4 is recorded, so a re-run would only add noise and cost. The real answers had no numeric errors, so injection is the only way to measure detection and repair | Re-running the full agent for v5; reporting "no effect" without testing the step |
| 72 | **A final per-version table with cost per question from the token ledger**, and "not measured" for versions that predate it | Every number must come from code that ran; v1/v2 token usage was never recorded | Back-filling v1/v2 cost from estimates |
| 73 | **The demo defaults to v5 and carries the model's own prose, citations and verification flag**; the v1–v3 rows were carried over from the previous demo DB (SHA-checked) | The old per-model answer caches are local files not in the repo; carrying rows over keeps them byte-identical instead of regenerating (which would cost calls) | Re-running v1–v3 for three models |
| 74 | **Dashboard live mode stays on v3 (no RAG)** | Streamlit Cloud has no local embedding model or knowledge index; a v5 live answer there would fail every time | Shipping torch to Cloud |
