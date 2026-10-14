# Score: late_orders

**Runs:** `claude -p --setting-sources "" --model sonnet --tools "" --output-format json` and the same with `--model haiku` (Claude Code 2.1.296),
isolated from this machine's hooks and global instructions, all tools disabled, one turn each, prompt = `prompt.md`
(seeds hidden). Sonnet is `claude-sonnet-5-5` (23 s, USD 0.06), response in `runs/sonnet-1.md`. Haiku is `claude-haiku-5-5`
(30 s, USD 0.005), response in `runs/haiku-1.md`.

## Claude Sonnet

**Root cause: found.** The incremental filter anchored to `max(order_date)` rather than arrival, six late February rows,
which days are missing or short, the 6 orders and 65.00, and why `unique` and `not_null` on the daily table cannot see
missing rows. It said it could not confirm the late rows without the seed. Unprompted, it flagged the missing `unique`
on `raw_customer_regions` again.

**Fix: made the output correct.** `daily_revenue` as a plain table, with `dbt run --full-refresh -s daily_revenue` as
the immediate repair. Both applied: 105 orders, 1737.00, February 33 and 480.00. Its full-outer-join reconciliation
test fails with 6 rows on the broken data, passes after either fix and on clean data (41/41).

**Verdict:** pass on both root cause and fix.

## Claude Haiku

**Root cause: found.** The same chain, plus the point that the "payments can land a day or two late" comment is
misleading because `orders` already absorbs late payments on every run.

**Fix: made the output correct.** The same table materialization: 105 orders, 1737.00, February 33 and 480.00. Its
reconciliation test fails with 6 rows on the broken data, passes after the fix and on clean data (41/41).

**Verdict:** pass on both root cause and fix.

**Earlier runs (default config, confounded by the ponytail hook, 17 to 22 Oct):** Sonnet pass and pass; Haiku not run.

## Repeat runs

Two more runs per model, same command, prompt assembled by `check_case.py` from the week 3 working tree.

| Run | Root cause | Fix | Notes |
|---|---|---|---|
| sonnet-2 | yes | yes | Full refresh then table. |
| sonnet-3 | yes | yes | Same. |
| haiku-2 | yes | yes | Same; notes the model's comment describes the wrong mechanism. |
| haiku-3 | yes | yes | Same. |

## Opus runs (week 6)

Three runs of `claude-opus-5-5`, same command, prompt assembled by `check_case.py` from the project at f769fdf, before
the week 6 lookup extension, so the same project the other two models saw; verdicts in `results.csv`.

| Run | Root cause | Fix | Notes |
|---|---|---|---|
| opus-1 | yes | yes | Six late February orders outside the three-day window; full refresh, then a table or a `_loaded_at`-driven window. |
| opus-2 | yes | yes | Same; also offers an incremental filter that rebuilds any day whose totals differ from `orders`, which works. |
| opus-3 | yes | yes | Same. |

Opus: 3 of 3 found, 3 of 3 fixed.

## Standard sample

**Runs:** three per model, week 8, on the standard output sample (the same queries on every case: row counts, the raw
column list, monthly totals of the output tables and ten rows of every table picked by hash); `runs/standard/prompt.md`
is the frozen prompt every run answered and `runs/standard/` holds the responses. `claude -p --setting-sources ""
--model <model> --tools "" --output-format json` (Claude Code 2.1.299), project ad0b16b. Same rubric; Defects is how
many of the case's injected defects the run named as a cause.

| Run | Root cause | Fix | Defects | Notes |
|---|---|---|---|---|
| sonnet-1 | yes | yes | 1 | Six February orders backfilled weeks after `daily_revenue` had written those days, outside the `max(order_date) - interval 3 day` window in `daily_revenue.sql`; `dbt run --full-refresh -s daily_revenue` then a plain table, with a load-timestamp window as the incremental alternative; could not name the six ids without the seed. |
| sonnet-2 | yes | yes | 1 | Same chain, six February orders and their payments landing in `raw_orders` and `raw_payments` after February was written; full refresh then table, with a 90-day lookback var as the incremental alternative; attaches a query to list the ids. |
| sonnet-3 | yes | yes | 1 | Same; late or backfilled February rows in `raw_orders` or `raw_payments` outside the window; full refresh then table, and updates the `schema.yml` description; attaches a per-day reconciliation query to confirm. |
| haiku-1 | yes | yes | 1 | Late-arriving February orders outside the window, rules out late payments because the count differs and `orders.amount` has no nulls; table materialization with a normal `dbt run` rebuilding February, no separate full refresh; says the model comment describes the wrong protection. |
| haiku-2 | yes | yes | 1 | Same; February orders arriving in `raw_orders` or `raw_payments` after the table was built, called an inference with a confirm query; full refresh then table, declines to tune the window. |
| haiku-3 | yes | yes | 1 | Same; rules out the cents conversion, `region_revenue` fan-out and null amounts; table then `dbt build --select daily_revenue`, plus the `schema.yml` description; attaches a per-day reconciliation query. |
| opus-1 | yes | yes | 1 | Orders dated February loaded after April, read from the sample ids 102, 103 and 105 carrying February dates above the original sequence; `dbt build -s daily_revenue --full-refresh` then table, or a `loaded_at`-driven window at scale. |
| opus-2 | yes | yes | 1 | Six February orders added to `raw_orders.csv` and `raw_payments.csv`, from the 105 and 119 row counts against the stock 99 and 113 and the sampled ids; full refresh then table, or an incremental filter that also rebuilds any day whose totals differ from `orders`, which works. |
| opus-3 | yes | yes | 1 | Same, ids 100-105 back-dated to February; full refresh then `materialized='table'`, or the same reconciling incremental filter, or a `_loaded_at` column on the raw feeds. |

**Verification.** Every fix matches a shape in the Verification paragraph above.

**What it says.** All nine standard runs found the late February rows and gave the same full-refresh-then-table fix as the nine curated runs, so the standard sample cost nothing on this case. The decisive evidence for every run was the monthly breakdown of `orders` against `daily_revenue` (33 and 480.00 against 27 and 415.00, other months equal) together with `customers` and `region_revenue` summing to 1737, which showed only the incremental table was stale; Sonnet and Haiku stopped there, said they could not see the six orders and attached confirm queries. Opus also used the `raw_orders` and `orders` sample rows, where ids 102, 103 and 105 carry February dates above the stock sequence, and the 105 and 119 seed counts against 99 and 113, to name the backfilled rows concretely. None went wrong.
