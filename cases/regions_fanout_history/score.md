# Score: regions_fanout_history

**Runs:** three per model, `claude -p --setting-sources "" --model <model> --tools "" --output-format json` (Claude Code 2.1.296),
isolated from this machine's hooks, tools off, prompt assembled by `check_case.py` with the seeds hidden and a models-and-tests
build log (`Log: models only`, a real `dbt build --exclude resource_type:seed`), project bef6b69; each run header carries the
sha256 of the prompt it answered. Sonnet is `claude-sonnet-5-5`, Haiku is `claude-haiku-5-5`; responses in `runs/`.
Scored on the week 4 rubric: root cause found yes/no (the run names the change that broke the data), complete fix yes/no (strict,
every affected value right after the fix).

| Run | Root cause | Fix | Notes |
|---|---|---|---|
| sonnet-1 | yes | yes | History feed, one row per region change, joined on `customer_id` alone; latest row per customer in a `stg_customer_regions` model (`updated_at desc nulls last, region`), left join with an UNASSIGNED bucket; noticed from the log header that the two seed tests did not run. |
| sonnet-2 | yes | yes | Same; `qualify row_number()` on `updated_at desc, region`; left join with UNKNOWN. |
| sonnet-3 | yes | yes | Same; ties on `updated_at` raised with the feed owner. |
| haiku-1 | yes | yes | Same; staging model with a tie-breaker caveat; `updated_at` added to the seed docs. |
| haiku-2 | yes | yes | Same; a `customer_current_region` model with `unique` and `not_null`, and a check that no ordering customer lacks a region row. |
| haiku-3 | yes | yes | Same; reconciles the 68 customers and 119 orders to the six moved customers. |

**Verification.** The latest-row dedupe reproduces the expected table exactly (62 customers, 99 orders, 1672.00 by
current region). Keeping the oldest row reproduces the clean table instead, which is the wrong report now that the
customers have moved; an as-of join on order date also sums to 1672.00 but puts the moved customers' earlier orders
in their old regions, so a total-only reconciliation would not tell the two apart. The `unique` test on the seed
fails with 6 customers on the broken data, the revenue reconciliation with 1 row; both pass clean.

**What it says.** Six of six, and every run chose the current region because the prompt said finance reports by it.
Without that sentence the as-of join would be a defensible alternative; the ground truth records both.

## Opus runs (week 6)

Three runs of `claude-opus-5-5`, same command, prompt assembled by `check_case.py` from the project at f769fdf, before
the week 6 lookup extension, so the same project the other two models saw; verdicts in `results.csv`.

| Run | Root cause | Fix | Notes |
|---|---|---|---|
| opus-1 | yes | yes | The feed is a change history and customers who moved count in both regions (+356 revenue, +20 order-region pairs, +6 customer-region pairs); latest row per customer in `stg_customer_regions` with tests, `region_revenue` reads it. |
| opus-2 | yes | yes | Same. |
| opus-3 | yes | yes | Same; also declares the table a source and left-joins with an UNKNOWN bucket, same output. |

Opus: 3 of 3 found, 3 of 3 fixed.

## Standard sample

**Runs:** three per model, week 8, on the standard output sample (the same queries on every case: row counts, the raw
column list, monthly totals of the output tables and ten rows of every table picked by hash); `runs/standard/prompt.md`
is the frozen prompt every run answered and `runs/standard/` holds the responses. `claude -p --setting-sources ""
--model <model> --tools "" --output-format json` (Claude Code 2.1.299), project ad0b16b. Same rubric; Defects is how
many of the case's injected defects the run named as a cause.

| Run | Root cause | Fix | Defects | Notes |
|---|---|---|---|---|
| sonnet-1 | yes | yes | 1 | History feed, 106 rows for 100 customers with an `updated_at` column, joined on `customer_id` alone so the six moved customers count in both regions; latest row per customer in a new `stg_customer_regions` (`row_number` on `updated_at desc, region`) that `region_revenue` reads; `unique` on the staging model plus a revenue-and-customers reconciliation to `orders`. |
| sonnet-2 | yes | yes | 1 | Same; `row_number` on `updated_at desc, region`; a left join with an unknown bucket offered as optional; reconciliation test fails 2028 against 1672 and 68 against 62 before the fix. |
| sonnet-3 | yes | yes | 1 | Same; `qualify row_number()` on `updated_at desc, region`; reconciles revenue, orders and customers to `orders`; a `severity: warn` `unique` on the seed suggested as an extra. |
| haiku-1 | yes | yes | 1 | Same; `qualify row_number()` on `updated_at desc`; read the log header (32 tests found, 28 run) and flagged the four seed tests that never executed; the left join left to finance. |
| haiku-2 | yes | yes | 1 | Same; `row_number` on `updated_at desc nulls last`; switches to a left join with an Unknown bucket, same output since every ordering customer has a region row; also flagged the four skipped seed tests. |
| haiku-3 | yes | yes | 1 | Same; reconciles the 6 extra customers, 20 extra orders and 356.00 extra revenue to the six extra rows; `qualify row_number()` on `updated_at desc`, ties left to fail the `unique` test; also flagged the four skipped seed tests. |
| opus-1 | yes | yes | 1 | Same; `qualify row_number()` on `updated_at desc, region`; reconciliation of revenue, customers and orders to `orders`. |
| opus-2 | yes | yes | 1 | Same; `qualify row_number()` on `updated_at desc`; `relationships` test to `stg_customers` on the staging model. |
| opus-3 | yes | yes | 1 | Same; `qualify row_number()` on `updated_at desc, region`; left join with an Unknown bucket offered conditionally; `unique_combination_of_columns` on `customer_id, updated_at` suggested. |

**Verification.** Every fix matches a shape in the Verification paragraph above.

**What it says.** Nine of nine found and fixed, matching the nine curated runs in score.md, and every run again chose the current region because the prompt says finance reports by it. The standard sample carried what the curated queries had been chosen to show: the row-count query (106 `raw_customer_regions` rows against 100 `raw_customers`), the column list with `updated_at`, the `customers` summary (62 with orders, 99 orders, 1672.0) and the `region_revenue` table summing to 68, 119 and 2028, and every run reasoned from 106 - 100 = 6 = 68 - 62 to a history feed. The ten-row `raw_customer_regions` sample includes customer 71 ACT 2018-03-01, one of the injected rows, but no run pointed to it; the three Haiku runs also read the log header (32 tests found, 28 run) and flagged the four skipped seed tests, which only the curated sonnet-1 had done.
