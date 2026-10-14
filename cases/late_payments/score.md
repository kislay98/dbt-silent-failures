# Score: late_payments

**Runs:** three per model, `claude -p --setting-sources "" --model <model> --tools "" --output-format json` (Claude Code 2.1.296),
isolated from this machine's hooks, tools off, prompt assembled by `check_case.py` with the seeds hidden and the seed lines dropped
from the build log (`Log: models only`), project bef6b69. Sonnet is `claude-sonnet-5-5`, Haiku is `claude-haiku-5-5`; responses in `runs/`.
Scored on the week 4 rubric: root cause found yes/no (the run names the change that broke the data), complete fix yes/no (strict,
every affected value right after the fix).
The runs answered the week 4 form of the models-only log (seed lines filtered out of the full build log, so node numbers started at 5 of 40); since T1-shlok-30 the harness builds that log with `--exclude resource_type:seed`, so `--prompt` now shows 34 nodes numbered from 1. Nothing else in the prompt changed.

| Run | Root cause | Fix | Notes |
|---|---|---|---|
| sonnet-1 | yes | yes | Late payments (ids 114 to 116 against 35 to 41 for the same orders) and the three-day window; full refresh now, table materialisation, a `loaded_at` on payments if it must stay incremental; reconciliation test by day. |
| sonnet-2 | yes | yes | Same, with the per-order table of original and late legs; notes the sample only shows three of the eight days' payments. |
| sonnet-3 | yes | yes | Same; drops the incremental config entirely. |
| haiku-1 | yes | yes | Same; maps payments 114, 115, 116 to the 02-02, 02-04 and 02-10 gaps. |
| haiku-2 | yes | yes | Same; also updates the stale model description. |
| haiku-3 | yes | yes | Same; insists the reconciliation test must cover all history, not a window. |

**Verification.** `dbt build --full-refresh --select daily_revenue` on the broken data makes `daily_revenue` equal the
day-by-day aggregate of `orders` (1742.00, February 485.00), and a table materialisation does the same on every run.
The reconciliation test fails with 8 rows on the broken data, passes clean and passes after the fix. A 90-day lookback,
which no run proposed, would also have reached back to 9 January.

**What it says.** Six of six. The late payment ids in the sample and the model's own comment ("a day or two") gave
every run the mechanism, and all six chose to drop the incremental logic rather than widen the window.

## Opus runs (week 6)

Three runs of `claude-opus-5-5`, same command, prompt assembled by `check_case.py` from the project at f769fdf, before
the week 6 lookup extension, so the same project the other two models saw; verdicts in `results.csv`.

| Run | Root cause | Fix | Notes |
|---|---|---|---|
| opus-1 | yes | yes | Late second payments (114 and up) on February orders, the window keyed on order date; full refresh, then a table or a changed-day rebuild. |
| opus-2 | yes | yes | Same. |
| opus-3 | yes | yes | Same, with an incremental variant that writes only changed days. |

Opus: 3 of 3 found, 3 of 3 fixed.

## Standard sample

**Runs:** three per model, week 8, on the standard output sample (the same queries on every case: row counts, the raw
column list, monthly totals of the output tables and ten rows of every table picked by hash); `runs/standard/prompt.md`
is the frozen prompt every run answered and `runs/standard/` holds the responses. `claude -p --setting-sources ""
--model <model> --tools "" --output-format json` (Claude Code 2.1.299), project ad0b16b. Same rubric; Defects is how
many of the case's injected defects the run named as a cause.

| Run | Root cause | Fix | Defects | Notes |
|---|---|---|---|---|
| sonnet-1 | yes | yes | 1 | Late February payments landing in `raw_payments` after those days left the three-day window in `daily_revenue.sql`; `dbt run --full-refresh -s daily_revenue` then `materialized='table'`; could not name the late payment rows without the seed and attaches a per-day diff query. |
| sonnet-2 | yes | yes | 1 | Same; full refresh then table with the `is_incremental()` block removed, a `loaded_at` on `raw_payments` if it must stay incremental; reads the matching count as `sum(amount)` skipping nulls. |
| sonnet-3 | yes | yes | 1 | Same, February orders first built while their payments were missing or partial; full refresh then drops the config to the project default table, with a 60-day lookback on `current_date` as the alternative at scale. |
| haiku-1 | yes | yes | 1 | Same; drops the config block so the model inherits the project default table and a plain `dbt run` rebuilds February, no separate full refresh; flags the missing `unique` on `raw_customer_regions` and the `total_order_amount` doc drift as side notes. |
| haiku-2 | yes | yes | 1 | Same, rules out the `amount / 100` division and `raw_payment_methods` fan-out; table plus one `--full-refresh`, and updates the `schema.yml` description. |
| haiku-3 | yes | yes | 1 | Same, "a late or corrected payment for a February order" as the amount change on existing orders; drops the config to table and reruns, updates the `schema.yml` description; notes the seed tests missing from the log. |
| opus-1 | yes | yes | 1 | Late `raw_payments` rows for February orders, pointing at sampled payment 120 on order 48 as a high id on a late-February order; `dbt build --select daily_revenue --full-refresh` then table, or a `loaded_at`-driven rebuild of touched days. |
| opus-2 | yes | yes | 1 | Same, payment 120 on order 48 and 121 payments for 99 orders; full refresh then table, `loaded_at` at scale, updates the `schema.yml` description. |
| opus-3 | yes | yes | 1 | Same, 121 payments against the stock 113 so the late rows are the highest ids; full refresh then table, with a `_loaded_at` filter rebuilding every day that received a payment since the last run as the incremental variant. |

**Verification.** Every fix matches a shape in the Verification paragraph above.

**What it says.** All nine standard runs found the late February payments and gave the same full-refresh-then-table fix as the nine curated runs, so this case is unchanged by the standard sample. The symptom text (same 27 orders on both sides, 485.00 against 415.00, settlement 485.00) and the monthly tables carried every run: the matching count with a 70.00 revenue gap told them only payment amounts had changed, and `customers` and `region_revenue` at 1742 told them `orders` was right. The standard sample shows only one of the eight late ids (payment 120 on order 48, where the curated sample showed 114 to 116 beside the originals); Opus used it and the 121 count against the stock 113, while Sonnet and Haiku said they could not see the late rows and attached confirm queries. None went wrong.
