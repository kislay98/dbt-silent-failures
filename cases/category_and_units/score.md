# Score: category_and_units

**Runs:** three per model, `claude -p --setting-sources "" --model <model> --tools "" --output-format json` (Claude Code 2.1.296),
isolated from this machine's hooks, tools off, prompt assembled by `check_case.py` with the seeds hidden and a models-and-tests
build log (`Log: models only`, a real `dbt build --exclude resource_type:seed`), project 8ee5734 (week 6 committed, with the
lookup extension); each run header carries the sha256 of the prompt it answered. Sonnet is `claude-sonnet-5-5`, Haiku is
`claude-haiku-5-5`, Opus is `claude-opus-5-5`; responses in `runs/`.
Scored on the week 4 rubric: root cause found yes/no (the run names the change that broke the data), complete fix yes/no (strict,
every affected value right after the fix).

| Run | Root cause | Fix | Notes |
|---|---|---|---|
| sonnet-1 | no | no | `delivered` from 10 March behind the widened lists, mapped in staging with the lists restored; the 1.98 residue noticed and read as "a cents/dollars issue on small coupon payments" for order 76 only, "which I can't confirm", with a query for ingestion; the coupon fix not made. |
| sonnet-2 | yes | yes | Both: `delivered` never treated as completed, and coupon rows loaded in dollars on orders 76, 81, 86, 92, 94 and 95, with order 76 closing the gap to 136.00 exactly; an `is_completed` flag on `orders` used by `customers`, the six rows reloaded in cents at the source, `daily_revenue` full refresh. |
| sonnet-3 | yes | yes | Both, the second "likely" from the six sub-dollar coupons all on or after 20 March; `delivered` mapped in staging, lists restored, coupons reloaded in cents by the ingestion job, full refresh. |
| haiku-1 | no | no | `delivered` found and the filter widened in `customers`; the 1.98 "I can't explain from these samples", left to the admin's order list; a `unique` test on the lookup as a latent risk. |
| haiku-2 | no | no | "Can't name a single root cause yet": stale tables, late payments or admin rules, with queries to decide; never reads the `delivered` rows in the sample; dedupes the regions feed as a latent defect. |
| haiku-3 | no | no | Same shape: missing March payments or orphan payments, the `delivered` rows unread; a `relationships` test proposed. |
| opus-1 | yes | yes | Both, with the 136.00 reconciled to the cent (134.02 plus the 1.98 on order 76); `delivered` mapped in staging with the source value kept, lists restored, coupon rows backfilled times 100 from the first bad id, `dbt build --full-refresh`. |
| opus-2 | yes | yes | Both, with the six coupons tabulated against their real amounts; mapping in staging, lists restored, the rows found by `amount % 100 <> 0` and backfilled, full refresh; refuses a "coupon under 100 means dollars" heuristic in staging. |
| opus-3 | yes | yes | Both, with each customer's gap matched to one delivered order; mapping in staging, lists restored, `update raw_payments set amount = amount * 100 where payment_method = 'coupon' and amount < 100`, full refresh. |

**Verification.** `delivered` counted as completed (mapped in staging, or the filter in `customers` widened) with the six coupon rows times 100 (by id, from 15 March, or every coupon under 1.00, which the clean data never has) restores `customers`, `orders` and `region_revenue` to the cent. The status change alone leaves completed revenue at 1101.02; the coupon fix alone leaves it at 967.00. A reconciliation of completed revenue to the admin, a check for coupon payments under 1.00 and the original five-value `accepted_values` list each fail on the broken data and pass clean.

**What it says.** Five of nine found and fixed. The second defect sits behind the first as a 1.98 residue, and the runs split on whether to chase it: Opus three for three and two Sonnet runs read the six sub-dollar coupons in the listing and closed the 136.00 to the cent; one Sonnet run and one Haiku run saw the residue and left it unexplained; two Haiku runs never read the `delivered` rows at all and proposed stale tables or orphan payments, the first Haiku misses on a status change the sample shows (3 of 3 on `status_new_value`).

## Standard sample

**Runs:** three per model, week 8, on the standard output sample (the same queries on every case: row counts, the raw
column list, monthly totals of the output tables and ten rows of every table picked by hash); `runs/standard/prompt.md`
is the frozen prompt every run answered and `runs/standard/` holds the responses. `claude -p --setting-sources ""
--model <model> --tools "" --output-format json` (Claude Code 2.1.299), project ad0b16b. Same rubric; Defects is how
many of the case's injected defects the run named as a cause.

| Run | Root cause | Fix | Defects | Notes |
|---|---|---|---|---|
| sonnet-1 | yes | yes | 2 | Both: `delivered` in March behind the widened lists, and coupon rows in dollars on payments 98 and 86 and order 94, read off the fractional coupon totals and the stale `daily_revenue` gap (26.73 as 27 less 0.27), though "the two delivered coupon rows at 2 each account for the missing 1.98" (it is one row, order 76); a `completed_statuses` var widening the filter in `customers`, `update raw_payments set amount = amount * 100 where amount % 100 <> 0`, full refresh. |
| sonnet-2 | yes | yes | 2 | Both: `delivered` never treated as completed, and newer coupon rows in whole dollars, with payment 86 lifting delivered from 134.02 to 136.00; an `is_completed` flag in `stg_orders` used by `customers`, and the coupon fix as a `stg_payments` passthrough (`when payment_method = 'coupon' and amount % 100 <> 0 then amount else amount / 100.0`) recommended as the stopgap behind a source fix, full refresh; "the stopgap rule is a guess based on the whole-dollar pattern, so check it against the raw feed". |
| sonnet-3 | yes | yes | 2 | Both: `delivered` not counted as completed behind the widened lists, and a few coupon rows in dollars (2, 23, 7) confirmed by the 59.40 `daily_revenue` gap being 0.99 times 60; `raw_payments` reloaded from the source in cents with no staging heuristic, an `is_completed` flag in `stg_orders` used by `customers`, full refresh, the rows "inferred from the output tables and need confirming with the queries at the end". |
| haiku-1 | no | no | 0 | "Root cause: not settled from these files"; rules status out because the `accepted_values` tests passed, never reads the `delivered` row in the status breakdown, and puts the 136 upstream, "either orders the admin has that the ingestion load lacks or doesn't reflect, or a status the admin has changed since the load"; the `daily_revenue` gap read backwards as a stale incremental, plus unrun seed tests and missing `unique` tests on the lookups; no model change for the 136. |
| haiku-2 | no | no | 0 | "I can't name one file as its cause from this evidence": upstream orders, statuses or orphan payments, with queries to decide; the `delivered` row and the 23 and 2 coupon rows unread; the `daily_revenue` gap read as the stale table being wrong, and `daily_revenue` turned into a table as the one fix. |
| haiku-3 | no | no | 0 | Same shape: "I can't identify the exact 136.00 from these artifacts", missing orders, status drift or orphan payments; "`stg_payments` divides `amount` by 100 and has no null or unit anomalies in the sample" with the 23 and 2 coupon rows in the `raw_payments` sample; `daily_revenue` turned into a table, a `relationships` test proposed. |
| opus-1 | yes | yes | 2 | Both, with the 136.00 reconciled to the cent (134.02 plus the 1.98 on order 76) and the `daily_revenue` gaps read as 27 less 0.27 and 33 less 0.33; an ingestion reload in cents with `update raw_payments set amount = amount * 100 where amount % 100 <> 0` as the stopgap, an `is_completed` flag in `stg_orders` used by `customers`, full refresh. |
| opus-2 | yes | yes | 2 | Both, with `daily_revenue` recognised as still holding the correct pre-load numbers (1672.00 in total); the rows found by `amount % 100 <> 0` backfilled times 100, `delivered` mapped to `completed` in `stg_orders` with `source_status` kept, lists restored, full refresh. |
| opus-3 | yes | yes | 2 | Both, with a reconciliation table and "fixing only the status gives 277.02, not 279"; the affected coupon rows (found by `amount % 100 <> 0`) reloaded in cents at ingestion, `delivered` mapped in staging, lists restored, full refresh, with a `stg_payments` passthrough (`coupon and amount % 100 <> 0 then amount`) offered only as a stopgap until the reload. |

**Verification.** `amount % 100 <> 0` selects exactly the six dollar rows on the broken data (checked against the injected seed), so sonnet-2's staging rule that leaves those rows undivided restores `stg_payments` like the source reload; every other fix matches a shape above.

**What it says.** Six of nine found and fixed against five of nine curated: Sonnet three for three (two curated), Opus three for three, Haiku none. The standard sample carries the `delivered` row in the status breakdown (8 orders, 134.02), the 23 and 2 coupon rows in the `raw_payments` sample, the 0.07 coupon on order 94, the fractional March and April coupon totals, and a stale `daily_revenue` still holding the clean 622.00 and 139.00 against 595.27 and 106.33 in `orders`; the six runs that found it used all of these, closing the 1.98 on order 76 and reading the 26.73 and 32.67 gaps as 27 less 0.27 and 33 less 0.33, which the curated runs could not do without the stale table. All three Haiku runs never read the `delivered` row, ruled status out because "the `accepted_values` tests on `stg_orders` and `orders` passed", put the 136 upstream of dbt, and read the `daily_revenue` gap backwards as a stale table to rebuild; haiku-3 even states `stg_payments` "has no null or unit anomalies in the sample".
