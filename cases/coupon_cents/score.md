# Score: coupon_cents

**Runs:** three per model, `claude -p --setting-sources "" --model <model> --tools "" --output-format json` (Claude Code 2.1.296),
isolated from this machine's hooks, tools off, prompt assembled by `check_case.py` with the seeds hidden and the seed lines dropped
from the build log (`Log: models only`), project bef6b69. Sonnet is `claude-sonnet-5-5`, Haiku is `claude-haiku-5-5`; responses in `runs/`.
Scored on the week 4 rubric: root cause found yes/no (the run names the change that broke the data), complete fix yes/no (strict,
every affected value right after the fix).
The runs answered the week 4 form of the models-only log (seed lines filtered out of the full build log, so node numbers started at 5 of 40); since T1-shlok-30 the harness builds that log with `--exclude resource_type:seed`, so `--prompt` now shows 34 nodes numbered from 1. Nothing else in the prompt changed.

| Run | Root cause | Fix | Notes |
|---|---|---|---|
| sonnet-1 | yes | yes | Coupon rows in dollars from 2018-03-20 on (orders 76 to 95), the seven earlier coupons still in cents; update the six rows at the source, full-refresh `daily_revenue`, no heuristic in staging. Tests: ledger reconciliation and a month-on-month scale drift check. |
| sonnet-2 | yes | yes | Mixed units with the boundary between orders 58 and 76; fix the six rows (and any later ones) at the source. Tests: payments under 1.00 in staging, finance totals as a seed. |
| sonnet-3 | yes | yes | Same six rows, found with `amount < 100`, fixed by explicit ids; ledger test plus the sub-dollar guard at `severity: warn`. |
| haiku-1 | yes | yes | Same diagnosis with a row-by-row reconciliation (18 + 2 + 2 + 23 = 45); update the six rows, full refresh; ledger test. |
| haiku-2 | no | no | Decided the March and April coupon payments never reach `orders` (orphan or NULL `order_id`) although the sample shows them at 0.02; proposed `relationships` tests and reloading the orders. |
| haiku-3 | yes | yes | Same six rows; update after confirming the ids; ledger test plus a "coupon rows under 100 cents" guard. |

**Verification.** Multiplying the six rows by 100, whether selected by payment id (86 and later), by an order-date boundary
anywhere between 2 and 20 March, or by `amount < 100`, restores `orders`, `customers` and `region_revenue` to the clean
values. The blanket "times 100 on every coupon row" that no run proposed pushes the seven earlier coupons to 100 times
their value (customer 3 to 2639.00). The sub-dollar test fails with 6 rows on the broken data and passes clean; the
ledger test fails on March and April; a "raw amount under 100 on any method" test would also fire on the three
zero-amount payments in the clean data.

**What it says.** Five of six on both counts. The miss is a direction error: Haiku-2 read the gap as missing rows rather
than shrunken ones and never looked at the 0.02 values in front of it. The partial switch did not trouble the other
five: all of them placed the boundary from the one cents row still in March (order 58).

## Opus runs (week 6)

Three runs of `claude-opus-5-5`, same command, prompt assembled by `check_case.py` from the project at f769fdf, before
the week 6 lookup extension, so the same project the other two models saw; verdicts in `results.csv`.

| Run | Root cause | Fix | Notes |
|---|---|---|---|
| opus-1 | yes | yes | Six coupon rows in dollars from about 20 March, order 58 still in cents; update the six at the source, full refresh; a sub-dollar test and the ledger reconciliation. |
| opus-2 | yes | yes | Same; the update takes its ids from an inspect query (`amount < 100` on coupons). |
| opus-3 | yes | yes | Same; the ids from an `ordered_at >= 2 March` coupon query, which selects exactly the six. |

Opus: 3 of 3 found, 3 of 3 fixed.

## Standard sample

**Runs:** three per model, week 8, on the standard output sample (the same queries on every case: row counts, the raw
column list, monthly totals of the output tables and ten rows of every table picked by hash); `runs/standard/prompt.md`
is the frozen prompt every run answered and `runs/standard/` holds the responses. `claude -p --setting-sources ""
--model <model> --tools "" --output-format json` (Claude Code 2.1.299), project ad0b16b. Same rubric; Defects is how
many of the case's injected defects the run named as a cause.

| Run | Root cause | Fix | Defects | Notes |
|---|---|---|---|---|
| sonnet-1 | yes | yes | 1 | Coupon rows in `raw_payments` in whole dollars under the blanket `amount / 100` in `stg_payments`, with payments 98 and 86 and order 94 read off the sample and the 27 and 33 solved from the ledger and the stale `daily_revenue` (26.73 as 27 times 0.99); the rows found by `amount % 100 <> 0` reloaded in cents at the source, full refresh, with a `stg_payments` passthrough on `amount % 100 <> 0` (all methods) offered only as an optional stopgap. |
| sonnet-2 | yes | yes | 1 | Same diagnosis, the bad rows "23, 2, 7" against the multiples of 100; the ids listed by `payment_method = 'coupon' and amount % 100 <> 0`, confirmed against the ledger, then `update raw_payments set amount = amount * 100 where id in (confirmed ids)`, the staging heuristic refused, full refresh. |
| sonnet-3 | yes | yes | 1 | Same, with payment 86 miscalled "id 76" and `daily_revenue` read as the pre-restatement values; `update raw_payments set amount = amount * 100 where payment_method = 'coupon' and amount < 100` on orders from 1 March (order 58's coupon is in cents, so the six), after an inspect query, full refresh, and `daily_revenue` made a table. |
| haiku-1 | yes | no | 1 | The cause named (coupon payments in mixed units under the `/ 100`) but the rows invented from the arithmetic without reading the sample: "two coupon payments appear to have been loaded as whole dollars (27 and 33) instead of cents (2700 and 3300)"; the fix is to "correct the two rows to cents (2700 and 3300) at the source", found by `amount in (27, 33)`, which matches no row, so nothing changes. |
| haiku-2 | no | no | 0 | Orphan payments: "the gap is in the payment-to-order link", coupon rows whose `order_id` is not in `raw_orders` dropped by the left join in `orders.sql`, though the sample shows them at 0.02 and 0.23; re-key or reload the orders, `daily_revenue` made a table, a `relationships` test. |
| haiku-3 | no | no | 0 | Same orphan theory, with units ruled out explicitly: "`stg_payments` divides by 100 correctly (DuckDB `/` returns DOUBLE, and the output shows 19.0 etc.)"; fix the `order_id` on the payments at the source, full refresh of `daily_revenue`, a `relationships` test on `stg_payments.order_id`. |
| opus-1 | yes | yes | 1 | Same diagnosis with the sample rows tabulated and January and February shown whole; `update raw_payments set amount = amount * 100 where payment_method = 'coupon' and amount % 100 <> 0` after an inspect query, full refresh, a `stg_payments` passthrough on `coupon and amount % 100 <> 0` as a stopgap only. |
| opus-2 | yes | yes | 1 | Same, with id 27 (2600) shown as the correct shape beside 98 and 86; ingestion fixed and the rows patched with `amount * 100 where payment_method = 'coupon' and amount % 100 <> 0` on orders from 1 March after an inspect query, the staging hack refused, full refresh. |
| opus-3 | yes | yes | 1 | Same, "starting in late March 2018"; every coupon row listed with its `ordered_at`, "the bad rows will be the non-multiples of 100 from late March onward", then `update raw_payments set amount = amount * 100 where payment_method = 'coupon' and id in (ids confirmed above)`, the `amount < 100` heuristic refused in staging, full refresh. |

**Verification.** `amount % 100 <> 0` (sonnet-1, opus-1, opus-2) selects exactly the six dollar rows on the broken data (checked against the injected seed), as `amount < 100` does apart from three zero payments it leaves at zero; every other fix matches a shape above.

**What it says.** Seven of nine found and six fixed, against eight of nine on both counts curated: Sonnet and Opus three for three each, Haiku one found and none fixed (two of three curated). The standard sample gave the runs two of the six rows (payments 98 and 86 at 23 and 2 beside 2600 on payment 27), the 0.07 coupon on order 94, and a stale `daily_revenue` still at the clean 622.00 and 139.00 against 595.27 and 106.33 in `orders`; the six successful runs read those rows and solved the 27 and 33 split from the ledger, and because the sample does not list the six ids they fixed by predicate (`amount % 100 <> 0` or `amount < 100` on coupons) or by ids from an inspect query, where the curated runs enumerated the six. The Haiku runs never used the sample rows: haiku-1 kept the cause but invented "two coupon payments ... (27 and 33)" from the monthly gaps, and haiku-2 and haiku-3 took the equal coupon and settlement gaps as orphan payments dropped by the join, haiku-3 ruling out the division because "the output shows 19.0 etc.".
