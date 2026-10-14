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
