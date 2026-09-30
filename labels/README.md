# Hand labels for judge calibration (Phase 8)

`human_labels.csv` holds 30 answers from the RAG analyst (prompt v4, gemini-3.1-flash-lite), drawn
by `python -m analyst.human_labels export`. It is **blind**: it shows neither the LLM judge's
verdict nor the eval grade. The instructions are also at the top of the CSV (lines starting with
`#`, which the reader ignores).

Fill in three columns per row:

| column | put | meaning |
|---|---|---|
| `faithful` | `1` / `0` | 1 if **every** number and factual claim in `answer` is supported by `sql_result`. Rounding and reformatting are fine (445.6018 → "£445.60", 0.387 → "38.7%", 17068582.72 → "£17.1 million"), and so is counting the result rows. Judge the answer against the result only, **not** whether the SQL was the right query. |
| `citation_correct` | `1` / `0` | 1 if the cited docs are current (not deprecated), relevant, and define what the SQL computes, **and** any needed definition doc among `docs_shown_to_agent` is cited. An empty citation list is correct only for plain column arithmetic. Docs are in `knowledge/<doc id>.md`. |
| `notes` | free text | optional; say why when unsure |

Then run `python -m analyst.agreement` (accuracy and Cohen's kappa, judge vs you) or tell Claude
the labels are done.

`human_labels_key.json` maps each `label_id` to its eval record (question id and run), so the
labels can be joined to the judge's verdicts afterwards. It contains no verdicts.
