# Score: join_drops_rows

**Runs:** three per model, `claude -p --setting-sources "" --model <model> --tools "" --output-format json` (Claude Code 2.1.296),
isolated from this machine's hooks, tools off, prompt assembled by `check_case.py` with the seeds hidden, project
7d5b525 plus the week 3 working tree. Sonnet is `claude-sonnet-5-5`, Haiku is `claude-haiku-5-5`; responses in `runs/`.
Scored on the week 4 rubric: root cause found yes/no (the run names the change that broke the data), complete fix yes/no (strict, every affected value right after the fix).

| Run | Root cause | Fix | Notes |
|---|---|---|---|
| sonnet-1 | yes | yes | Orphan payments dropped by the left join in `orders` and the inner join in `region_revenue`, 140.00, order 25's third payment. Said the key "may be wrong ids, or null" and that it cannot invent the ids. Fix: correct the references at the source, no model patch, full-refresh `daily_revenue`. |
| sonnet-2 | yes | yes | Same chain; adds that if orphans can be legitimate they should land in an "unknown" bucket rather than vanish. |
| sonnet-3 | yes | yes | Same chain; also names the leak in `customers`. |
| haiku-1 | yes | yes | Same chain; ruled out the cents conversion and a regions fan-out explicitly before settling. |
| haiku-2 | no | no | "The payments are fine. The orders are incomplete": attributes the orphans to orders missing from `raw_orders` and proposes loading them; calls order 25 "not explained". Its diagnostic uses `NOT IN (select id ...)`, which returns no rows when the key is NULL (0 rows against 13 with a left join, checked). |
| haiku-3 | yes | yes | Orphans dropped by the joins; guessed order 25's payment was keyed to a neighbouring order (wrong, hedged with a query); fix: resolve at the source either way. |

**Verification.** `not_null` on `stg_payments.order_id` fails with 13 rows on the broken data. dbt's `relationships` test
passes on it, because the test skips NULL keys, so a run that relied on `relationships` alone would have stayed blind;
every run paired it with `not_null`. The payments-against-orders reconciliation fails with 1 row on the broken data.
All of them pass on clean data (43/43). No run proposed a warehouse-side repair, which is right: the references are gone.

**What it says.** Five of six. The miss is a direction error, not a mechanism error: the model saw unmatched payments
and decided the orders were missing rather than the keys, and its own check would have hidden the evidence.

## Opus runs (week 6)

Three runs of `claude-opus-5-5`, same command, prompt assembled by `check_case.py` from the project at f769fdf, before
the week 6 lookup extension, so the same project the other two models saw; verdicts in `results.csv`.

| Run | Root cause | Fix | Notes |
|---|---|---|---|
| opus-1 | yes | yes | 13 payments whose key matches no order, the keys wrong rather than the orders missing; said "wrong ids, an offset or typo" rather than empty, but its listing query is a left join that finds the NULL keys; fix at the source from the processor mapping, no join change; `not_null` plus `relationships`. |
| opus-2 | yes | yes | Same; adds an unmatched-payments model and the staging-against-marts reconciliation. |
| opus-3 | no | yes | Re-run after a hung call, separate prompt build. 13 unmatched payments, then a hedge: mis-keyed payments or orders missing from `raw_orders`, "the query will tell"; the fix is at the source either way and its listing query shows NULL keys, so the branch the data picks restores the clean output. |

Opus: 2 of 3 found, 3 of 3 fixed. The third run was made separately after a hung call and its root-cause verdict is the one hedge in the Opus set: it offered orders-missing as an alternative to keys-missing instead of choosing.

## Standard sample

**Runs:** three per model, week 8, on the standard output sample (the same queries on every case: row counts, the raw
column list, monthly totals of the output tables and ten rows of every table picked by hash); `runs/standard/prompt.md`
is the frozen prompt every run answered and `runs/standard/` holds the responses. `claude -p --setting-sources ""
--model <model> --tools "" --output-format json` (Claude Code 2.1.299), project ad0b16b. Same rubric; Defects is how
many of the case's injected defects the run named as a cause.

| Run | Root cause | Fix | Defects | Notes |
|---|---|---|---|---|
| sonnet-1 | yes | yes | 1 | Named the NULL `order_id` rows in `raw_payments.csv` (raw id 91, stg payment 15) dropped by the joins in `orders.sql`, `customers.sql` and `region_revenue.sql`, 140.00, and read the stale `daily_revenue` at 1672.00 as the only record of where the orphans belonged; fix restores the `order_id` values from the source system, "don't guess them", then `daily_revenue` as a table or a full refresh; `not_null` plus `relationships` and a four-way reconciliation. |
| sonnet-2 | yes | yes | 1 | Same chain, order 25's third payment "has a NULL or non-matching order_id"; fix at the source from the payments feed or settlement with a full refresh of `daily_revenue+`, an unassigned bucket only as an alternative to failing tests; noted that `relationships` skips NULLs so `not_null` is needed. |
| sonnet-3 | yes | yes | 1 | Same chain with the 16.00 for order 25 as a payment that lost its `order_id`; fix restores the keys from the source, "Do not default them to some order", and makes `daily_revenue` a plain table; a payments-against-orders reconciliation. |
| haiku-1 | yes | yes | 1 | Put "the cause" in the `orders.sql` join pattern but named the NULL `order_id` rows 91 and 15 in `raw_payments` as the data fault, then extrapolated from the 10-row sample, "The remaining 117.00 must come from payments with order_id values that aren't in raw_orders", and guessed order 25's payment carries a wrong id; still broken keys on payments rather than missing orders, so found; fix corrects the keys from the processor feed with a full refresh of `daily_revenue`. |
| haiku-2 | yes | yes | 1 | Named unattributed payments with NULL `order_id` in `raw_payments` dropped at `orders.sql`, order 25's payment "a hypothesis until the query below confirms it"; fix re-keys them from the processor feed, "Don't infer mappings from amounts or dates", leaves `orders.sql` alone and adds an `unattributed_payments` model to surface any remainder. |
| haiku-3 | yes | yes | 1 | Same two NULL rows, order 25's missing payment as a NULL key in the feed; fix restores `order_id` from the processor feed without matching by amount or timing, adds an `unattributed_payments` model, full refresh of `daily_revenue`. |
| opus-1 | yes | yes | 1 | "The order_id column is blank on some rows of seeds/raw_payments.csv. The SQL models are not the problem"; rows 91 and 15, the 16.00 for order 25; fix restores the keys from the settlement file or git, warns not to full-refresh `daily_revenue` before the seed is fixed, then `dbt build --full-refresh -s raw_payments+`. |
| opus-2 | yes | yes | 1 | Same; the month gaps 38, 21, 79 and 2 summing to 140; fix restores from git or the settlement file with a full refresh. |
| opus-3 | yes | yes | 1 | Same; recognised payments 15 (order 13, bank_transfer 1400) and 30 (order 25, coupon 1600) from the upstream jaffle_shop seed, both right against the ground truth; fix restores from git or the settlement file with the rest checked against the source, full refresh. |

**Verification.** Every fix matches a shape in the Verification paragraph above.

**What it says.** Nine of nine found and fixed against seven found and eight fixed in the curated runs. The standard sample's `raw_payments` and `stg_payments` hash samples each happened to include a NULL `order_id` row (ids 91 and 15), so the direction the curated misses got wrong (keys blank on payments rather than orders missing from `raw_orders`) was given, not inferred, and every run also read the stale `daily_revenue` at 1672.00 against `orders` at 1532.00 as proof the blanking was recent; opus-2 matched the per-month gaps to 140 and several runs used the `raw_payments` row 28 (order 25, 2000) to place the lost 16.00. The only slip is haiku-1 treating the two sampled NULLs as the whole NULL population, "The remaining 117.00 must come from payments with order_id values that aren't in raw_orders", a wrong split in the right direction; opus-3 named payments 15 and 30 from memory of the upstream seed and both are right. Every fix went to the source and no run patched the joins.
