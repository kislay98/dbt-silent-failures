# Score: duplicate_payments

**Runs:** `claude -p --setting-sources "" --model sonnet --tools "" --output-format json` and the same with `--model haiku` (Claude Code 2.1.296),
isolated from this machine's hooks and global instructions, all tools disabled, one turn each, prompt = `prompt.md`
(seeds included, 28-test project). Sonnet is `claude-sonnet-5-5` (17 s, USD 0.07), response in `response.md`. Haiku is `claude-haiku-5-5`
(28 s, USD 0.006), response in `response_haiku.md`.

## Claude Sonnet

**Root cause: found.** It named `seeds/raw_payments.csv` ids 114-125, listed every duplicate with its original (all 12
pairs correct), the 180.00 gap against the settlement, orders 1 and 10 and customer 1, and why `unique` on
`payment_id` passes. It declined to dedupe in staging because order 18 legitimately has two credit card payments (500 and
800), which is a correct reading of the seed.

**Fix: made the output correct.** Delete rows 114-125. Applied and rebuilt: `sum(orders.amount)` 1672.0, customers 1 and
7 back to 33.0 and 26.0. Its first test is `dbt_utils.unique_combination_of_columns` on (`order_id`, `payment_method`,
`amount`); `dbt_utils` is not installed here, and the equivalent singular group-by returns 12 rows on the broken data
and 0 after the fix. Its second test reconciles to a `processor_settlements` seed that does not exist yet, which it said.

**Verdict:** pass on both root cause and fix.

## Claude Haiku

**Root cause: found.** The same table with one slip (it says id 119 duplicates id 50; it is 51), the 180.00 gap, orders
1, 10 and 18 and customers 1, 7 and 64 (all values correct), `INSERT 125` read off the log, and the point that
reconciling `orders` to `stg_payments` would not catch this because both sides carry the duplicates. It flagged the
stale `total_order_amount` doc line.

**Fix: made the output correct.** Remove rows 114-125: `sum(orders.amount)` 1672.0, customers 1, 7 and 64 at 33.0,
26.0 and 30.0. Its singular test on (`order_id`, `payment_method`, `amount`) returns 12 rows on the broken data and 0
after the fix.

**Verdict:** pass on both root cause and fix, at a tenth of Sonnet's cost.

**Earlier runs (default config, confounded by the ponytail hook, 17 to 22 Oct):** Sonnet pass and pass with two wrong
originals in its table; Haiku pass and pass. No verdict changed.
