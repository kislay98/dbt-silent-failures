# Score: duplicate_payments

**Runs:** `claude -p --setting-sources "" --model sonnet --tools "" --output-format json` and the same with `--model haiku` (Claude Code 2.1.296),
isolated from this machine's hooks and global instructions, all tools disabled, one turn each, prompt = `prompt.md`
(seeds included, 28-test project). Sonnet is `claude-sonnet-5-5` (17 s, USD 0.07), response in `runs/sonnet-1.md`. Haiku is `claude-haiku-5-5`
(28 s, USD 0.006), response in `runs/haiku-1.md`.

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

## Repeat runs

Two more runs per model, same command, prompt assembled by `check_case.py` from the week 3 working tree.

| Run | Root cause | Fix | Notes |
|---|---|---|---|
| sonnet-2 | yes | yes | Full table of originals, all correct; delete rows 114-125; optional staging dedupe after checking no legitimate pair exists. |
| sonnet-3 | yes | yes | Same; declines the staging dedupe on principle. |
| haiku-2 | yes | yes | Same table, all correct; customer 64 and order 18 numbers right. |
| haiku-3 | yes | yes | Same; checked ids 1 to 113 by hand for legitimate pairs. |

These four runs saw the prompt assembled from the grown project (40 tests, seeds shown), unlike run 1.

## Opus runs (week 6)

Three runs of `claude-opus-5-5`, same command, prompt assembled by `check_case.py` from the project at f769fdf, before
the week 6 lookup extension, so the same project the other two models saw; verdicts in `results.csv`.

| Run | Root cause | Fix | Notes |
|---|---|---|---|
| opus-1 | yes | yes | All twelve rows 114 to 125 with the right originals; delete and full refresh; declines the staging dedupe because the order 18 and 79 splits are legitimate; (order, method, amount) test. |
| opus-2 | yes | yes | Same table and fix. |
| opus-3 | yes | yes | Same table and fix. |

Opus: 3 of 3 found, 3 of 3 fixed.

## Standard sample

**Runs:** three per model, week 8, on the standard output sample (the same queries on every case: row counts, the raw
column list, monthly totals of the output tables and ten rows of every table picked by hash); `runs/standard/prompt.md`
is the frozen prompt every run answered and `runs/standard/` holds the responses. `claude -p --setting-sources ""
--model <model> --tools "" --output-format json` (Claude Code 2.1.299), project ad0b16b. Same rubric; Defects is how
many of the case's injected defects the run named as a cause.

| Run | Root cause | Fix | Defects | Notes |
|---|---|---|---|---|
| sonnet-1 | yes | yes | 1 | Full table of `raw_payments.csv` ids 114 to 125 with the right originals, the 180.00 against the settlement gap, order 1 as payments 1 plus 114; delete the rows and full refresh, with "confirm with finance first that they are not genuine repeat charges" as a check before applying rather than a condition; the staging dedupe on (`order_id`, `payment_method`, `amount`) offered only as optional; `unique_combination_of_columns` at warn, a settlement reconciliation against a seed that does not exist yet, which it said, and a `daily_revenue` rollup check. |
| sonnet-2 | yes | yes | 1 | Same table, all correct; delete rows 114 to 125 and full refresh; declines the staging dedupe because a legitimate repeat "would be silently dropped"; singular natural-key test plus the `daily_revenue` reconciliation. Reads the `daily_revenue` month table as stale, with the April 14.00 on order 97 inside the three-day window. |
| sonnet-3 | yes | yes | 1 | Same table; delete the rows and full refresh; the `qualify` dedupe in `stg_payments` is named as where dedup belongs in a real pipeline but made conditional on finance confirming equal splits never happen; singular natural-key test and the `daily_revenue` total check. |
| haiku-1 | yes | yes | 1 | Same table, with the file lines 115 to 126; delete the rows, `dbt seed`, full refresh of `daily_revenue`, then build; declines the staging dedupe as hiding the problem; `unique_combination_of_columns` on the triple plus a settlement reconciliation. Says of the stale `daily_revenue` "I can't confirm this from the log alone", which is the secondary issue, not the cause. |
| haiku-2 | yes | yes | 1 | Same table, with the month gaps 74, 35 and 57 tied to the duplicates' order dates and April inside the window; delete rows 114 to 125 after confirming against the settlement file, full refresh; declines the staging dedupe; singular natural-key test plus an `orders` to `stg_payments` reconciliation it admits would pass here. |
| haiku-3 | yes | yes | 1 | Same table; remove the rows, `dbt seed`, full refresh; declines the staging dedupe as dropping a real second payment; a custom generic test on the triple in `macros/`. Its expected monthly `orders.amount` after the fix (526, 403, 604, 139) is wrong except April, though the 1672.00 total is right. |
| opus-1 | yes | yes | 1 | Same table; delete the rows and full refresh, with a check with finance before deleting; a `qualify` dedupe on the triple offered as an optional guard, "Only add this if" equal splits can never happen; singular natural-key test and a four-way rollup reconciliation. Explains `daily_revenue` at 1686 as the pre-duplicate rows plus the 14.00 on order 97 rebuilt by the window. |
| opus-2 | yes | yes | 1 | Same table; delete rows 114 to 125 and full refresh; declines the staging dedupe because "order 18 has two credit-card payments (500 and 800), as do orders 49, 51 and 79", a correct reading of the seed; natural-key test at warn plus the `daily_revenue` reconciliation. Names each month's gap by order. |
| opus-3 | yes | yes | 1 | Same table; delete the rows and full refresh; declines the staging dedupe; natural-key test and a day-by-day `daily_revenue` reconciliation. |

**Verification.** Every fix matches a shape in the Verification paragraph above.

**What it says.** Nine of nine found and fixed, the same as the nine curated runs, and since this prompt carries the seeds as the curated one did, every run read the 12 appended rows off `raw_payments.csv` and listed all 12 originals correctly (the curated haiku-1 had one slip). The new material in the standard sample was the `daily_revenue` month table (496, 415, 622, 153 against 570, 450, 679, 153 in `orders`), which all nine read as a second, stale-incremental problem and six decomposed exactly (74, 35, 57 by month, the April 14.00 on order 97 pulled in by the three-day window); nobody used the `orders` sample row for order 54 with its doubled 36.0 credit card amount, and none of it distracted from the cause. Six of nine declined the staging dedupe on (`order_id`, `payment_method`, `amount`) and the other three offered it only as a conditional guard, opus-2 giving the reason best: "order 18 has two credit-card payments (500 and 800), as do orders 49, 51 and 79". The only slip anywhere is haiku-3's wrong expected monthly amounts after the fix.
