# Score: fanout_and_nulls

**Runs:** three per model, `claude -p --setting-sources "" --model <model> --tools "" --output-format json` (Claude Code 2.1.296),
isolated from this machine's hooks, tools off, prompt assembled by `check_case.py` with the seeds hidden and a models-and-tests
build log (`Log: models only`, a real `dbt build --exclude resource_type:seed`), project f769fdf plus the week 6 lookup
extension (committed with week 6); each run header carries the sha256 of the prompt it answered. Sonnet is `claude-sonnet-5-5`,
Haiku is `claude-haiku-5-5`, Opus is `claude-opus-5-5`; responses in `runs/`.
Scored on the week 4 rubric: root cause found yes/no (the run names the change that broke the data), complete fix yes/no (strict,
every affected value right after the fix).

| Run | Root cause | Fix | Notes |
|---|---|---|---|
| sonnet-1 | yes | yes | Both: two `credit_card` rows double the 853.00 of card payments that have an amount, eight NULL amounts drop 65.00 (+853 and -65 reconciled to the cent); remove the stale lookup row, the eight amounts reloaded by ingestion, the fee sum split into its own CTE, full refresh. |
| sonnet-2 | yes | yes | Both; a `max(fee_rate)` pre-aggregation as the guard, the eight amounts backfilled from the statement, no `coalesce`, full refresh; notes that a per-order reconciliation cannot see NULLs on its own. |
| sonnet-3 | yes | yes | Both; data fix for the lookup, amounts backfilled from the processor, full refresh; order 13 explained as 5.00 doubled plus 14.00 missing. |
| haiku-1 | yes | yes | Both; one row per method, re-ingest the eight amounts, a `row_number` guard in the model, full refresh; lists the expected totals after the fix. |
| haiku-2 | yes | yes | Both; filter the lookup to the settlement processor or fix the seed, correct the eight amounts upstream, full refresh. |
| haiku-3 | yes | yes | Both; dedupe the seed with finance, re-pull the eight amounts, full refresh; a per-method gap table that matches the ground truth. |
| opus-1 | yes | yes | Both, split cleanly into +853 (orders minus staging, the card total counted twice) and -65 (staging minus the processor, missing data); one row per method at the source, the eight amounts re-ingested in cents, a scalar-subquery fee lookup that errors on a duplicate, full refresh. |
| opus-2 | yes | yes | Both; the NULL counts per method matched to the shortfalls; data fixes for both, amounts computed from `payments` alone as hardening, full refresh. |
| opus-3 | yes | yes | Both; names three of the eight missing amounts from the reconciliation (15 = 1400, 30 = 1600, 67 = 600); one row per method, re-ingest, full refresh. |

**Verification.** One row per method in the lookup plus the eight amounts restored (ids 10, 15, 21, 30, 55, 58, 62, 67)
brings `orders`, `customers` and `region_revenue` back to the clean values. The lookup alone leaves `orders.amount` at
1607.00 with orders 13, 18, 25, 49, 51, 54 and 58 short; the amounts alone leave credit cards doubled at 2543.00. A
`unique` test on the lookup key fails with 1 row, `not_null` on `stg_payments.amount` with 8, the reconciliation with 1;
all pass clean.

**What it says.** Nine of nine on both counts, against 3 of 6 found on `units_and_rename`. The difference is visibility: here the NULL rows sat in the sample and the per-method shortfalls were in the complaint, so the second defect did not have to be inferred from a residue. Every run split the gap into +853 and -65 before naming either cause.
