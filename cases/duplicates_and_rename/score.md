# Score: duplicates_and_rename

**Runs:** three per model, `claude -p --setting-sources "" --model <model> --tools "" --output-format json` (Claude Code 2.1.296),
isolated from this machine's hooks, tools off, prompt assembled by `check_case.py` with the seeds hidden and a models-and-tests
build log (`Log: models only`, a real `dbt build --exclude resource_type:seed`), project 8ee5734 (week 6 committed, with the
lookup extension); each run header carries the sha256 of the prompt it answered. Sonnet is `claude-sonnet-5-5`, Haiku is
`claude-haiku-5-5`, Opus is `claude-opus-5-5`; responses in `runs/`.
Scored on the week 4 rubric: root cause found yes/no (the run names the change that broke the data), complete fix yes/no (strict,
every affected value right after the fix).

| Run | Root cause | Fix | Notes |
|---|---|---|---|
| sonnet-1 | no | no | The rename from payment 65 and the NULL method zeroing the pivot and the fees; `coalesce(payment_method, method)`, full refresh. Payments 68 and 114 "may be a legitimate split payment or a duplicate load", left to the processor statement. |
| sonnet-2 | no | no | Same; "if it's off by exactly 28.00 after the rebuild, look at payments 68 and 114", not decided. |
| sonnet-3 | no | no | Same; ids 114 to 118 "beyond the original range", "may be legitimate or a double load", to be compared with the statement; `--full-refresh` only "if you remove a duplicate". |
| haiku-1 | no | no | The rename only; payment 114 listed among the NULL rows without comment. |
| haiku-2 | no | no | The rename, and payment 114 "probably a duplicated payment row" with the downstream effect spelled out, then "if confirmed" remove it; the rule for finding the copies left open. |
| haiku-3 | no | no | The rename; the pair "may be a genuine second payment or a duplicate", to check after the rebuild against the 334.00. |
| opus-1 | no | no | The rename, with the `accepted_values` NULL semantics spelled out and the fees effect; the copies "unconfirmed": "if March card volume comes out above 334, look at payments with IDs above 113 first". |
| opus-2 | no | no | Same; "if the processor statement shows only one, it's a duplicate that inflates March", a separate issue to reconcile. |
| opus-3 | no | no | Same; ids 114 to 118 "look like re-delivered rows" with a query to confirm and a `qualify row_number()` dedupe as a stopgap, "only if a legitimate repeat payment of the same amount can't happen"; not applied. |

**Verification.** `coalesce(payment_method, method)` (or `method` alone) in `stg_payments` with the five copies removed, by id or as one row per order, method and amount, restores `orders`, `customers` and `region_revenue` exactly; no order in the clean data has two payments of the same method and amount, so the dedupe is safe. The rename fix alone leaves `sum(orders.amount)` at 1765.00 with the copies now counted in their method columns; dropping the copies alone leaves every method column at 0 from March. `not_null` on `stg_payments.payment_method` fails with 54 rows on the broken data, a uniqueness check on order, method and amount with 5, and both pass clean.

**What it says.** Nine of nine named the rename and none is scored found: every run saw payment 114 next to payment 68 in the sample and every run handed it back ("may be a legitimate split payment or a duplicate load", "if March card volume comes out above 334", "only if a legitimate repeat payment of the same amount can't happen"). The copy is in the sample but not in the complaint, and with tools off no run would act on it; the larger model went furthest and stopped at a conditional stopgap. The same shape as `rename_and_new_status`: the second defect is seen and not decided, so under the rubric it is a miss nine times.

## Standard sample

**Runs:** three per model, week 8, on the standard output sample (the same queries on every case: row counts, the raw
column list, monthly totals of the output tables and ten rows of every table picked by hash); `runs/standard/prompt.md`
is the frozen prompt every run answered and `runs/standard/` holds the responses. `claude -p --setting-sources ""
--model <model> --tools "" --output-format json` (Claude Code 2.1.299), project ad0b16b. Same rubric; Defects is how
many of the case's injected defects the run named as a cause.

| Run | Root cause | Fix | Defects | Notes |
|---|---|---|---|---|
| sonnet-1 | no | no | 1 | The rename from the sample (id 93 NULL against id 28 with both columns), the NULL method zeroing the pivot and the fees; `coalesce(payment_method, method)` and `dbt build --full-refresh`. The copies not seen; the 622.00 against 708.00 gap read backwards as a "secondary issue, inferred from the output rather than proven by the data" where `daily_revenue` "rows stay stale", so the refresh would push the one right table to 1765.00 too. |
| sonnet-2 | no | no | 1 | Same; `coalesce(payment_method, method)` and `dbt run --full-refresh -s daily_revenue`. The gap called "out of sync" with "I haven't confirmed why the rows went stale"; ids above 113 and 118 payments for 99 orders not remarked on. |
| sonnet-3 | no | no | 1 | Same; `coalesce(payment_method, method)` and `dbt build --full-refresh`. `daily_revenue` a "second problem" where "payments that landed later than that for older orders were never picked up"; "118 rows total" quoted in the test section without a word on the 99 orders. |
| haiku-1 | no | no | 1 | The rename, with ids 114 and 115 listed among the NULL rows without comment; `coalesce(payment_method, method)` and `dbt build -s daily_revenue --full-refresh`. The gap put on the incremental window, "an inference from the numbers, not something I've confirmed". |
| haiku-2 | no | no | 1 | The rename; `coalesce(payment_method, method)` and `dbt build --select daily_revenue+ --full-refresh`. "The per-day order counts match, so the amounts are stale", the gap pinned on `daily_revenue` rather than on `orders`. |
| haiku-3 | no | no | 1 | The rename, with three confirming queries it could not run; `coalesce(payment_method, method)` and `dbt build --select stg_payments+`. The gap under "other things I noticed": "older days don't pick up payments that landed late. This is a separate issue", with a full refresh of `daily_revenue`. |
| opus-1 | no | no | 1 | The rename, with ids 111 to 115 listed as new-feed rows without comment; `coalesce(payment_method, method)` and `dbt build -s stg_payments+ --full-refresh`. Saw that `daily_revenue` "total is 1672, versus 1765 in orders, customers and region_revenue" and still put it on the incremental window: "This has nothing to do with the column change". |
| opus-2 | no | no | 1 | The rename, with id 114 on order 59 in its own table of NULL rows; `coalesce(method, payment_method)` and `dbt build -s daily_revenue --full-refresh`. The gap read as "payments for older dates that arrived later (or were backfilled) never get picked up". |
| opus-3 | no | no | 1 | The rename, with id 115 on order 68 shown as a new-format row; `coalesce(method, payment_method)` and `dbt build --select stg_payments+`. A month table of `daily_revenue` against `orders` with "All other models total 1765", then "the incremental filter only rebuilds the last 3 days" and a full refresh. |

**Verification.** Every fix matches a shape in the Verification paragraph above.

**What it says.** Nine of nine named the rename and none the copies, the same count as the curated runs but a different miss: the curated sample put payment 114 beside payment 68 and every run handed the pair back, while the standard sample shows ids 114 and 115 on orders 59 and 68 without their originals, so no run considered a duplicate at all. The copies' only trace in the standard sample is the 93.00 by which `orders` (708.00 and 146.00) exceeds `daily_revenue` (622.00 and 139.00), and all nine read it backwards: the one table the copies do not reach was called stale ("payments that landed later than that for older orders were never picked up", "it is the incremental window") and all nine prescribed a full refresh that would push `daily_revenue` to 1765.00 too. 118 payments for 99 orders and ids above 113 went unremarked even where listed (haiku-1, opus-1, opus-2, opus-3) or quoted ("118 rows total", sonnet-3).
