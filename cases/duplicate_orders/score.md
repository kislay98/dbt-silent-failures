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

## Standard sample

**Runs:** three per model, week 8, on the standard output sample (the same queries on every case: row counts, the raw
column list, monthly totals of the output tables and ten rows of every table picked by hash); `runs/standard/prompt.md`
is the frozen prompt every run answered and `runs/standard/` holds the responses. `claude -p --setting-sources ""
--model <model> --tools "" --output-format json` (Claude Code 2.1.299), project ad0b16b. Same rubric; Defects is how
many of the case's injected defects the run named as a cause.

| Run | Root cause | Fix | Defects | Notes |
|---|---|---|---|---|
| sonnet-1 | yes | yes | 1 | The raw order feed loaded twice for part of February, 16 rows with ids 100 to 115 repeating an earlier `user_id` and `ordered_at`, read off the 41/105 pair and the February gap, payments replayed with them; `row_number` dedupe in `stg_orders` on (`user_id`, `ordered_at`) keeping the lowest id, `stg_payments` filtered to surviving orders, `daily_revenue` to a table or full refresh; natural-key test plus the day-by-day reconciliation. |
| sonnet-2 | yes | yes | 1 | Same block, ids 100 to 115 all in February, with order 114 as the customer 51 replay; delete at the source plus the same staging dedupe and payment filter, full refresh; asks for a check that the duplicates' payments are replays before trusting the settlement, a data check rather than a hedge; raw natural-key test and the reconciliation. |
| sonnet-3 | yes | yes | 1 | Same block dated 10 to 23 February, read off 41/105 and order 104 against customer 33's first order; staging dedupe on (`user_id`, `ordered_at`) with the payment filter, `daily_revenue` to a table, the raw rows cleared once ops confirm; calls the stale `daily_revenue` "a secondary issue". |
| haiku-1 | yes | yes | 1 | Names "two faults", the duplicate loads in `raw_orders` under new ids and the three-day lookback that never repairs February; dedupe in `stg_orders` on (`user_id`, `ordered_at`) keeping the lowest id with the replayed payments left to fall out of the `order_id` joins, `daily_revenue` to a table; natural-key and reconciliation tests. Does not state the id range. |
| haiku-2 | yes | yes | 1 | Ids 100 to 115 as copies with payments 114 to 131; staging dedupe keeping the lowest id, `stg_payments` filtered, full refresh only after the staging fix; `unique_combination_of_columns` plus the reconciliation, and unique tests on the two seed join keys. |
| haiku-3 | yes | yes | 1 | Duplicate rows in `raw_orders` loaded twice under new ids, 16 surplus all in February, offered as "a strong hypothesis, not a confirmed diagnosis" with the confirming query attached and no rival cause; `qualify row_number()` dedupe on (`user_id`, `ordered_at`) keeping the lowest id with payments following through the join, `daily_revenue` to a table; suggests picking the survivor by settled payments "not just `min(id)`" but the code keeps the lowest id, which is the original. |
| opus-1 | yes | yes | 1 | 16 orders re-ingested as ids 100 to 115 with 18 re-sent payments; deletes both at the source by the natural-key self-join on (`user_id`, `ordered_at`) keeping the lower id, full refresh; the staging dedupe is offered as optional and argued against ("Silently deduplicating hides the ingestion bug"); natural-key singular test plus the reconciliation. |
| opus-2 | yes | yes | 1 | 16 February orders re-sent as ids 100 to 115 with payments 114 to 131, noting every sampled id above 99 is dated 10 to 23 February; source delete of the ids from the self-join query plus a `qualify` dedupe in `stg_orders` and the `stg_payments` filter, full refresh of `daily_revenue`; natural-key and reconciliation tests. |
| opus-3 | yes | yes | 1 | Same block and payment ids; source delete plus staging dedupe on (`customer_id`, `ordered_at`) with `stg_payments` inner-joined to the surviving orders, full refresh; says only the 41/105 pair is confirmed directly and the other 15 are inferred from the totals, with the confirming query attached. |

**Verification.** Every fix matches a shape in the Verification paragraph above.

**What it says.** Nine of nine found and fixed, the same as the nine curated runs. Where the curated prompt handed over three visible pairs to reconstruct the block from, the standard sample gave one (`stg_orders` rows 41 and 105, customer 99 at `2018-02-14 10:44:12`) plus the month table, which puts the whole gap in February (43 against 27 orders, 656.0 against 415.0, exactly 16 and 241.0), and every run built its diagnosis from those two; four (haiku-2, opus-1, opus-2, opus-3) also read the payment ids off the `raw_payments` rows 114, 127 and 131 pointing at orders 100, 113 and 115. Every run read the stale `daily_revenue` as a second, latent issue and fixed it, as all nine curated runs did. None went wrong; the only softening was haiku-3's "a strong hypothesis, not a confirmed diagnosis", with the confirming query attached.
