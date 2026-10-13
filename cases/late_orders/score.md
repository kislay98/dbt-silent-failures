# Score: late_orders

**Runs:** `claude -p --setting-sources "" --model sonnet --tools "" --output-format json` and the same with `--model haiku` (Claude Code 2.1.296),
isolated from this machine's hooks and global instructions, all tools disabled, one turn each, prompt = `prompt.md`
(seeds hidden). Sonnet is `claude-sonnet-5-5` (23 s, USD 0.06), response in `response.md`. Haiku is `claude-haiku-5-5`
(30 s, USD 0.005), response in `response_haiku.md`.

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
