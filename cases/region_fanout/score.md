# Score: region_fanout

**Runs:** `claude -p --setting-sources "" --model sonnet --tools "" --output-format json` and the same with `--model haiku` (Claude Code 2.1.296),
isolated from this machine's hooks and global instructions, all tools disabled, one turn each, prompt = `prompt.md`
(seeds hidden). Sonnet is `claude-sonnet-5-5` (10 s, USD 0.05), response in `runs/sonnet-1.md`. Haiku is `claude-haiku-5-5`
(23 s, USD 0.004), response in `runs/haiku-1.md`.

## Claude Sonnet

**Root cause: found.** 108 regions rows for 100 customers (read off the seed line in the build log), a one-to-many join
before the aggregate in `region_revenue.sql`, the 174.00 overcount, counts hidden by `count(distinct ...)`, and the
missing `unique` test on the seed. One wrong guess, hedged with a confirmation query: "a duplicated customer will also
show up in two regions". The re-sent rows repeat the same region.

**Fix: made the output correct.** A new `stg_customer_regions` keeping the latest row per customer with `row_number`,
and `region_revenue` reading it. Applied: every region matches the clean data. Its tests: `unique` on the staging key
(passes after the dedupe, by design) and, in parentheses, `unique` on the raw key, which fails with 8 rows on the broken
data; its reconciliation test against `orders` fails with 1 row on the broken data, passes after the fix and on clean
data (41/41).

**Verdict:** pass on both root cause and fix.

## Claude Haiku

**Root cause: found.** The same inference from 108 against 100 and the `count(distinct ...)` columns, plus the deduction
that the duplicates must be same-region because the regional order total is exactly 99. Its confirmation query returns
the 8 customers with one distinct region each.

**Fix: made the output correct.** `stg_customer_regions` with `qualify row_number()`, and `region_revenue` rewritten over
`orders` instead of payments so it cannot fan out. Applied: every region, customer count and order count matches the
clean data. Its reconciliation test (subquery form) fails with 1 row on the broken data and passes after the fix.

**Verdict:** pass on both root cause and fix.

**Earlier runs (default config, confounded by the ponytail hook, 17 to 22 Oct):** Sonnet pass and pass; Haiku not run.
Hiding the seed did not slow either model: the log prints the seed row count. The next fan-out case strips it.

## Repeat runs

Two more runs per model, same command, prompt assembled by `check_case.py` from the week 3 working tree.

| Run | Root cause | Fix | Notes |
|---|---|---|---|
| sonnet-2 | yes | yes | Dedupe inside the `regions` CTE. |
| sonnet-3 | yes | yes | Staging model with a tie-breaker. |
| haiku-2 | yes | yes | Dedupe inside the CTE; this run predates the project stamp in the run header. |
| haiku-3 | yes | yes | Same. |

Same fix shapes as the verified runs.

## Opus runs (week 6)

Three runs of `claude-opus-5-5`, same command, prompt assembled by `check_case.py` from the project at f769fdf, before
the week 6 lookup extension, so the same project the other two models saw; verdicts in `results.csv`.

| Run | Root cause | Fix | Notes |
|---|---|---|---|
| opus-1 | yes | yes | 108 region rows for 100 customers read off the seed log, re-sent rows with the same region since `sum(orders)` stays 99; latest row per customer in a new `stg_customer_regions` with `unique` and `relationships` tests, `region_revenue` reads it; revenue reconciliation to `orders`. |
| opus-2 | yes | yes | Same; names the as-of join as the alternative if finance wants the region at order time. |
| opus-3 | yes | yes | Same. |

Opus: 3 of 3 found, 3 of 3 fixed.
