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

## Standard sample

**Runs:** three per model, week 8, on the standard output sample (the same queries on every case: row counts, the raw
column list, monthly totals of the output tables and ten rows of every table picked by hash); `runs/standard/prompt.md`
is the frozen prompt every run answered and `runs/standard/` holds the responses. `claude -p --setting-sources ""
--model <model> --tools "" --output-format json` (Claude Code 2.1.299), project ad0b16b. Same rubric; Defects is how
many of the case's injected defects the run named as a cause.

| Run | Root cause | Fix | Defects | Notes |
|---|---|---|---|---|
| sonnet-1 | no | no | 1 | The window keyed on `order_date` and the six-order, 65.00 gap reconciled, but the regions feed never considered: "The other models (`customers`, `region_revenue`) are full rebuilds and aren't affected." Full refresh and `daily_revenue` as a table, a load timestamp if it must stay incremental, the per-day reconciliation test; the rebuild alone leaves the regional report at 1838.00. |
| sonnet-2 | yes | yes | 2 | Same on `daily_revenue`, then "Separate issue, not the cause of this mismatch": `region_revenue` at 1838.00 against 1737.00, `raw_customer_regions` 106 rows for 100 customers read as a history feed from `updated_at`, the join fans out. Full refresh and table; for the regions one line, "Take the latest row per customer", no code, plus `unique` on `customer_id`. |
| sonnet-3 | yes | yes | 2 | Same on both, the regions join "double-counts their payments"; table and full refresh, dedupe to the latest row per `customer_id` in `region_revenue`, `unique` on the seed column. |
| haiku-1 | yes | yes | 2 | Names "a second, separate bug" in `region_revenue.sql` with the 101.0 of duplicated revenue, and sees the late-payments comment is wrong since revenue comes from `orders.amount`. `daily_revenue` made a table and run plain, the regions deduped with `qualify row_number()` on `updated_at desc` inside `region_revenue` ("please confirm with the feed owner" as an assumption note), the reconciliation and `unique` tests. |
| haiku-2 | yes | yes | 2 | Same on both, the regions under "Other issue I noticed"; full refresh and table, "I'd fix it by keeping the latest row per customer" with the `row_number` window and a `unique` test, offered to apply on request. |
| haiku-3 | yes | yes | 2 | Same on both with the sharpest reading of the regions, customer counts unaffected so "the extra rows repeat the same region for a customer", and spots 32 tests found against 28 run as the seed tests not running. Full refresh and table, a `stg_customer_regions` model keeping the latest `updated_at` row with `region_revenue` repointed to it, the reconciliation and `unique` tests. |
| opus-1 | no | no | 1 | The window on `order_date` with backfilled ids 102, 103 and 105 read off the `orders` sample; headline "the raw data is fine" and `region_revenue` never mentioned. Full refresh and table, or an incremental filter that also rebuilds any date whose totals no longer match `orders`, the reconciliation test; the rebuild alone leaves the regional report at 1838.00. |
| opus-2 | yes | yes | 2 | Same on `daily_revenue` with the February ids; "Separate issue: `region_revenue` is overstated", 1838.00 against the 1737.00 lifetime value, 106 rows for 100 customers and customer 2's 2018-04-02 row in the sample. Full refresh and table, `qualify row_number()` to the latest row per customer before the join, `unique` on the seed or a staging model. |
| opus-3 | yes | yes | 2 | Same on both, customer 2's 2018-04-02 row cited; full refresh and table with the `schema.yml` description updated, `qualify row_number()` keeping the latest row per customer, `unique` on `customer_id`. |

**Verification.** Every fix matches a shape in the Verification paragraph above.

**What it says.** Seven of nine found and fixed, against nine found and seven fixed on the curated prompt. The standard sample carried the whole second defect: `raw_customer_regions` at 106 rows against 100 customers in the count table, `updated_at` in the column list, the regional report summing to 1838.00 against 1737.00 in the `orders` month table and `customers` lifetime value, and customer 2's 2018-04-02 row; seven runs added the seven regions up themselves and named the fan-out, two of them citing customer 2. The two misses never summed the regions: sonnet-1 wrote "The other models (`customers`, `region_revenue`) are full rebuilds and aren't affected" and opus-1 headlined "the raw data is fine". The late half was never in doubt, and where two curated Sonnet runs left the regions with a test and no change, every standard run that saw the fan-out put the dedupe in the fix.
