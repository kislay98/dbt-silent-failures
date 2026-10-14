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

## Standard sample

**Runs:** three per model, week 8, on the standard output sample (the same queries on every case: row counts, the raw
column list, monthly totals of the output tables and ten rows of every table picked by hash); `runs/standard/prompt.md`
is the frozen prompt every run answered and `runs/standard/` holds the responses. `claude -p --setting-sources ""
--model <model> --tools "" --output-format json` (Claude Code 2.1.299), project ad0b16b. Same rubric; Defects is how
many of the case's injected defects the run named as a cause.

| Run | Root cause | Fix | Defects | Notes |
|---|---|---|---|---|
| sonnet-1 | yes | yes | 2 | Both: the second `credit_card` row fans out the join in `orders.sql`, NULL amounts in `raw_payments` (ids 30 and 55 from the sample) drop 65.00, the 1607.00 of `customers` and `region_revenue` read as the true total less the NULLs and the 835 card excess less 47 reconciled to the 788 gap. `max(fee_rate)` per method before the join or drop the seed row, the NULL amounts re-ingested from the processor statements, full refresh of `daily_revenue`; `unique`, `not_null` and a per-order reconciliation. |
| sonnet-2 | yes | yes | 2 | Both, 1706 as 2 x 853 and 871 less 18 of NULL card amounts; `max(fee_rate)` pre-aggregation in `orders.sql`, the NULL rows reloaded by ingestion with no `coalesce`, the adyen row dropped or a processor column added, full refresh. Says the NULLs are unconfirmed order by order with the listing query attached, a data check rather than a hedge. |
| sonnet-3 | yes | yes | 2 | Both, order 13 explained as 5.00 doubled plus a NULL worth 14.00, and spots the 28 of 32 tests as the seed tests not running; remove the duplicate seed row, ingestion reloads the NULL amounts, `max(fee_rate)` dedupe as the guard, `dbt build --full-refresh`; `unique`, `not_null` and a per-method reconciliation to `stg_payments`. |
| haiku-1 | yes | yes | 2 | Both, with CC = 853 and N = 754 solved from the two totals and the seed tests seen missing from the log; one row per method in the feed plus `max(fee_rate)` as the guard, the NULL amounts re-pulled from the processor, full refresh; `unique`, `not_null`, reconciliation. Order 13's missing payment put at "the other 9.00" rather than 14.00. |
| haiku-2 | no | yes | 1 | The fan-out named as primary with 2460 = 1607 + 853; the 65.00 shortfall hedged, "Two likely causes", NULL amounts or orphan payments without an order, "I can't see the full table, so I can't confirm which, or both." One row per method at the source with no dedupe in the model, NULL amounts and orphans fixed at ingestion, `dbt build --full-refresh`, which restores every value since there are no orphans; `unique`, `not_null`, per-order reconciliation and an orphan check. |
| haiku-3 | yes | yes | 2 | Both, B "inferred from the arithmetic and the sample" with the confirming queries attached, and the seed tests seen not running; remove the inactive processor's row, fix the ingestion of the NULL amounts, `qualify row_number()` per method as the guard, full refresh of `daily_revenue`; per-order reconciliation, `unique`, `not_null`. |
| opus-1 | yes | yes | 2 | Both, the table splitting 2460 into 1672 less 65 plus 853 and order 13 as 5.00 doubled with 14.00 NULL; the NULL rows reloaded or backfilled from the statement, `max(fee_rate)` per method before the join, full refresh of `daily_revenue+`; `unique`, `not_null`, reconciliation, noting the reconciliation alone passes while the NULLs remain. Also flags the `total_order_amount` column documented in `schema.yml` but not output. |
| opus-2 | yes | yes | 2 | Both, and alone in reading `daily_revenue` right, January to March "built before either fault existed and match the settlement" and April 163 against 139 as 24 of doubled card; the NULL amounts backfilled from the processor ("their sum should come to 6500 cents"), the lookup made one row per method at the source plus `max(fee_rate)` in the model, full refresh; `unique`, `not_null`, reconciliation, and the seed tests put back in the nightly selector. |
| opus-3 | yes | yes | 2 | Both, the reconciliation table to the cent and the expected values after the fix (1672.00, 871.00, order 2 at 20.00, order 13 at 19.00); the NULL rows reloaded, `max(fee_rate)` per method with the stale feed row deleted too, full refresh of `orders+ daily_revenue`; `unique`, `not_null`, reconciliation. |

**Verification.** Every fix matches a shape in the Verification paragraph above.

**What it says.** Eight of nine found and nine of nine fixed, against nine and nine on the curated prompt. The standard sample carried both defects in the open as the curated one did: the two `credit_card` rows in the `raw_payment_methods` sample (5 rows in the count table), NULL `amount` on payments 30 and 55 in the `raw_payments` and `stg_payments` samples, the 1607.00 of `customers` and `region_revenue` against 2460.00 to split the gap into +853 and -65, and payment 14 at 500 cents to read order 13; every run split the gap before naming either cause. The one miss is haiku-2, which hedged the 65.00 between the NULLs and orphan payments: "I can't see the full table, so I can't confirm which, or both." Eight runs read `daily_revenue` as stale when its January to March rows are the clean values and only opus-2 saw it as the pre-fault baseline, at no cost since all nine full-refresh it after the fix.
