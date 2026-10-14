# Score: duplicate_orders

**Runs:** three per model, `claude -p --setting-sources "" --model <model> --tools "" --output-format json` (Claude Code 2.1.296),
isolated from this machine's hooks, tools off, prompt assembled by `check_case.py` with the seeds hidden and the seed lines dropped
from the build log (`Log: models only`), project bef6b69. Sonnet is `claude-sonnet-5-5`, Haiku is `claude-haiku-5-5`; responses in `runs/`.
Scored on the week 4 rubric: root cause found yes/no (the run names the change that broke the data), complete fix yes/no (strict,
every affected value right after the fix).
The runs answered the week 4 form of the models-only log (seed lines filtered out of the full build log, so node numbers started at 5 of 40); since T1-shlok-30 the harness builds that log with `--exclude resource_type:seed`, so `--prompt` now shows 34 nodes numbered from 1. Nothing else in the prompt changed.

| Run | Root cause | Fix | Notes |
|---|---|---|---|
| sonnet-1 | yes | yes | A replayed block of 16 orders, ids 100 to 115 repeating 36 to 51 (offset 64, read off the three visible pairs), payments replayed with them; delete both at the source, full-refresh `daily_revenue`, make it a table; business-key test plus the day-by-day reconciliation. |
| sonnet-2 | yes | yes | Same mechanism (places the block at 37 to 52, one off); deletes by the business-key self-join rather than by id range, which is right either way; refuses a staging dedupe as "silently dropping legitimate orders"; `unique_combination_of_columns` on (customer, ordered_at). |
| sonnet-3 | yes | yes | Same block; delete at the source plus a defensive `qualify row_number()` dedupe in staging, `daily_revenue` to a table. |
| haiku-1 | yes | yes | 16 copies with fresh ids and copied payments, `daily_revenue` "showing the pre-duplicate figures"; delete at the source, table materialisation. Noticed the stripped seed lines from the node numbering ("the log starts at 5 of 40"). |
| haiku-2 | yes | yes | Same; dedupes in `stg_orders` on (customer, ordered_at, status) keeping the lowest id and rewrites `customer_payments` to start from orders; reconciliation and natural-key tests. |
| haiku-3 | yes | yes | Same, ids 100 to 115 inferred from the counts; remove at the raw layer after sign-off, make the loader idempotent, full-refresh `daily_revenue`. |

**Verification.** Removing orders 100 to 115 and their payments restores every table (that is the clean seed). A staging
dedupe on (customer, ordered_at, status) or on (customer, ordered_at), with the payments following through the join or
dropped explicitly, restores `orders`, `customers`, `region_revenue` and the day-by-day aggregate too: the replayed
payments are keyed to the dropped ids and never reach a customer. The business-key test fails with 16 rows on the
broken data, the day-by-day reconciliation with 11 days, the admin count check with 1 row; all pass clean.

**What it says.** Six of six with the seeds hidden and the seed row counts gone from the log: every run reconstructed
the replayed block from the three visible pairs and the arithmetic (16 rows, 241.00). All six also read the stale
`daily_revenue` as a second defect, the three-day lookback, and fixed it.

## Opus runs (week 6)

Three runs of `claude-opus-5-5`, same command, prompt assembled by `check_case.py` from the project at f769fdf, before
the week 6 lookup extension, so the same project the other two models saw; verdicts in `results.csv`.

| Run | Root cause | Fix | Notes |
|---|---|---|---|
| opus-1 | yes | yes | 16 copies found by the natural key; guessed the id range as 101 to 116 (it is 100 to 115) but deletes by the natural-key join, which removes exactly the 16 and their payments; full refresh. |
| opus-2 | yes | yes | Ids 100 to 115 for 36 to 51, natural-key delete, full refresh; warns that a staging dedupe would leave the replayed payments in a NULL-customer group. |
| opus-3 | yes | yes | `row_number` dedupe into a temp table, delete, full refresh. |

Opus: 3 of 3 found, 3 of 3 fixed.
