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

## Standard sample

**Runs:** three per model, week 8, on the standard output sample (the same queries on every case: row counts, the raw
column list, monthly totals of the output tables and ten rows of every table picked by hash); `runs/standard/prompt.md`
is the frozen prompt every run answered and `runs/standard/` holds the responses. `claude -p --setting-sources ""
--model <model> --tools "" --output-format json` (Claude Code 2.1.299), project ad0b16b. Same rubric; Defects is how
many of the case's injected defects the run named as a cause.

| Run | Root cause | Fix | Defects | Notes |
|---|---|---|---|---|
| sonnet-1 | yes | yes | 1 | 108 region rows for 100 customers read off the row counts, the `region_revenue.sql` join on `customer_id` fans out before `sum(payments.amount)`, same region deduced from the 62 and 99 count sums; a new `stg_customer_regions` keeping the latest `updated_at` per customer with `qualify row_number()`, `region_revenue` reads it; `unique` on the staged key and on the seed, revenue reconciliation to `orders`. |
| sonnet-2 | yes | yes | 1 | Same; `stg_customer_regions` with a `region` tie-breaker, `region_revenue` reads it, revenue from `orders` offered as the cheaper alternative; `unique` on the staged key, reconciliation. |
| sonnet-3 | yes | yes | 1 | Same count, but reads the duplicates as "customers who moved region" and expects "a few regions will drop" after the fix, where the re-sent rows repeat the region; dedupe inside the `regions` CTE with `row_number()` on `updated_at` desc plus a left join with a `coalesce(region, 'unknown')` bucket, which matches the clean output since every ordering customer has a region row; `unique` on the seed key and reconciliation. |
| haiku-1 | yes | yes | 1 | Same, with the duplicates deduced to differ only in `updated_at`; `stg_customer_regions` with `qualify row_number()`, `region_revenue` reads it, an upstream fix kept as the option; `unique` on the seed key and a reconciliation restricted to customers with a region. |
| haiku-2 | yes | yes | 1 | Same; `stg_customer_regions` with `qualify row_number()`, `region_revenue` reads it, ties on `updated_at` and ordering customers without a region flagged to check; reconciliation and `unique` on the staged key. |
| haiku-3 | yes | yes | 1 | Same; dedupe inside the `regions` CTE with `qualify row_number()` and revenue from `orders.amount` over `orders` instead of payments; reconciliation and `unique` on the seed key at `warn`. |
| opus-1 | yes | yes | 1 | Same; `stg_customer_regions` keeping the latest row with `unique` and `relationships` tests, `region_revenue` rewritten over `orders` with `count(order_id)` so a future fan-out shows in the counts too; reconciliation of revenue and order counts; names the as-of join if finance wants the region at order time. |
| opus-2 | yes | yes | 1 | Same; dedupe inside the `regions` CTE with `qualify row_number()` and revenue over `orders`; says fixing the CSV alone "would break again the next time the feed is refreshed"; reconciliation and `unique` on a deduped relation. |
| opus-3 | yes | yes | 1 | Same; `stg_customer_regions` with a `region` tie-breaker, `region_revenue` reads it, revenue over `orders` optional; `unique` and `relationships` on the staged key, `unique` on the seed at `warn`, reconciliation. |

**Verification.** Every fix matches a shape in the Verification paragraph above.

**What it says.** Nine of nine on both counts, the same as the curated runs. The standard sample hands over the count twice, `raw_customer_regions` at 108 in the row-count table and `INSERT 108` in the seed log, and every run started from 108 rows for 100 customers; seven then deduced that the re-sent rows carry the same region because the per-region customer and order counts still sum to 62 and 99, and most attached a confirmation query for the duplicated customers. Only sonnet-3 used the two `2018-04-02` rows in the ten-row seed sample, and read them wrong: "the extra 8 rows come from customers who moved region", with a prediction that "a few regions will drop" after a fix that in fact restores every region. All nine fixes keep the latest `updated_at` per customer (six in a new `stg_customer_regions`, three inside the `regions` CTE) and four rewrite revenue over `orders`; none removes the rows at the source.
