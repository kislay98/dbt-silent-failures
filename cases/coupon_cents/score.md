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
