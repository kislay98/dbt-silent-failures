# Score: fanout_and_late

**Runs:** three per model, `claude -p --setting-sources "" --model <model> --tools "" --output-format json` (Claude Code 2.1.296),
isolated from this machine's hooks, tools off, prompt assembled by `check_case.py` with the seeds hidden and a models-and-tests
build log (`Log: models only`, a real `dbt build --exclude resource_type:seed`), project 8ee5734 (week 6 committed, with the
lookup extension); each run header carries the sha256 of the prompt it answered. Sonnet is `claude-sonnet-5-5`, Haiku is
`claude-haiku-5-5`, Opus is `claude-opus-5-5`; responses in `runs/`.
Scored on the week 4 rubric: root cause found yes/no (the run names the change that broke the data), complete fix yes/no (strict,
every affected value right after the fix).

| Run | Root cause | Fix | Notes |
|---|---|---|---|
| sonnet-1 | yes | no | The window keyed on order date, the six days reconciled to the cent; then "a separate issue (not verified)": `region_revenue` 101.00 over `orders`, "probably a join fan-out from `raw_customer_regions`" with its `updated_at`. `daily_revenue` rebuilds any day that no longer matches `orders`; the regions get a `unique` test and no dedupe. |
| sonnet-2 | yes | no | Same diagnosis on both; `daily_revenue` as a table with a full refresh; for the regions only a query to run, no change. |
| sonnet-3 | yes | yes | Same; table, full refresh; "if it confirms, keep the latest row per customer in `region_revenue` and add `unique`". |
| haiku-1 | yes | yes | "Separate bug, same gap": the fan-out named with the 101.00 and a query to verify; table, full refresh, the regions deduped to the latest `updated_at` in a staging model, `unique` on `customer_id`. |
| haiku-2 | yes | yes | Same; table; dedupe to one row per customer before the join, `unique` added; notes the 0.03 s incremental run in the log. |
| haiku-3 | yes | yes | Same; table; dedupe in a staging model; a `region_revenue` to `orders` reconciliation test as the second guard. |
| opus-1 | yes | yes | Same, with an incremental alternative that rebuilds any date whose aggregate no longer matches `orders`; `qualify row_number() ... = 1` on the regions, `unique` on `customer_id`. |
| opus-2 | yes | yes | Same; the `customers` sample used to confirm the six orders; table, or a `loaded_at` watermark from ingestion; dedupe to the latest row plus a reconciliation test. |
| opus-3 | yes | yes | Same; table; "dedupe to the latest `updated_at` per customer in the model or fix the feed", `unique` test. |

**Verification.** `daily_revenue` rebuilt from `orders` with one region row per customer in `region_revenue` (the latest `updated_at`, or distinct customer and region) gives 105 orders and 1737.00 in all three tables. The rebuild alone leaves the regional report at 1838.00; the dedupe alone leaves the dashboard at 1672.00; restoring the seeds removes six real orders. The per-day reconciliation fails with 6 rows, `unique` on `raw_customer_regions.customer_id` with 6 and a `region_revenue` to `orders` reconciliation with 1, all passing clean.

**What it says.** Nine of nine found, seven fixed. With the regional report in the sample and 101.00 over `orders`, every run named the fan-out through the regions feed from the `updated_at` column alone, with a query it could not run to confirm; seven put the dedupe in the fix and two Sonnet runs left the regions with a test and no change. The late half was never in doubt.
