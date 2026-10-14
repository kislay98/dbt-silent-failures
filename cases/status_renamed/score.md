# Score: status_renamed

**Runs:** `claude -p --setting-sources "" --model sonnet --tools "" --output-format json` and the same with `--model haiku` (Claude Code 2.1.296),
isolated from this machine's hooks and global instructions, all tools disabled, one turn each, prompt = `prompt.md`
(seeds hidden). Sonnet is `claude-sonnet-5-5` (15 s, USD 0.05), response in `runs/sonnet-1.md`. Haiku is `claude-haiku-5-5`
(20 s, USD 0.004), response in `runs/haiku-1.md`.

## Claude Sonnet

**Root cause: found.** Two status columns in `raw_orders`, the 43 March and April orders with `status` NULL,
`stg_orders.sql` selecting only `status`, the case expression in `customers.sql` treating NULL as not completed, 824
against 1103, `accepted_values` ignoring NULL and no `not_null` on status. It asked for the vocabulary of `order_status`
to be confirmed first; on the broken data it carries the same five values.

**Fix: made the output correct.** `coalesce(status, order_status) as status`. Applied with the downstream models re-run:
every customer's `completed_lifetime_value` matches the clean data. Its `not_null` tests on both status columns fail
with 43 rows each on the broken data and pass after the fix. Its singular test (both columns NULL) returns 0 rows on the
broken data: a guard for the next rename, not a detector for this one, which it said.

**Verdict:** pass on both root cause and fix.

## Claude Haiku

**Root cause: found.** The same chain, and it said it could not confirm from the sample that exactly 279.00 of the NULL
group was completed. Its confirmation query shows it: 17 completed orders in the NULL group, 279.00.

**Fix: made the output correct.** The same `coalesce`. Applied: every customer matches the clean data. Its `not_null`
tests fail with 43 rows each on the broken data and pass after the fix.

**Verdict:** pass on both root cause and fix.

**Earlier runs (default config, confounded by the ponytail hook, 17 to 22 Oct):** Sonnet pass and pass; Haiku not run.

## Repeat runs

Two more runs per model, same command, prompt assembled by `check_case.py` from the week 3 working tree.

| Run | Root cause | Fix | Notes |
|---|---|---|---|
| sonnet-2 | yes | yes | `coalesce`; its drift test (`status` NULL and `order_status` not NULL) fails with 43 rows on the broken data, checked. |
| sonnet-3 | yes | yes | `coalesce`; says itself that a customers-against-orders reconciliation would not catch this. |
| haiku-2 | yes | yes | `coalesce` after a disagreement check. |
| haiku-3 | yes | yes | `order_status as status`, which works because the column is backfilled. |

## Opus runs (week 6)

Three runs of `claude-opus-5-5`, same command, prompt assembled by `check_case.py` from the project at f769fdf, before
the week 6 lookup extension, so the same project the other two models saw; verdicts in `results.csv`.

| Run | Root cause | Fix | Notes |
|---|---|---|---|
| opus-1 | yes | yes | The feed moved the status to `order_status` from March, 0 of 43 rows with a status, `accepted_values` skips NULL; `coalesce(status, order_status)` in `stg_orders` after checking the vocabulary; `not_null` next to `accepted_values`. |
| opus-2 | yes | yes | Same. |
| opus-3 | yes | yes | Same; a two-column consistency test. |

Opus: 3 of 3 found, 3 of 3 fixed.

## Standard sample

**Runs:** three per model, week 8, on the standard output sample (the same queries on every case: row counts, the raw
column list, monthly totals of the output tables and ten rows of every table picked by hash); `runs/standard/prompt.md`
is the frozen prompt every run answered and `runs/standard/` holds the responses. `claude -p --setting-sources ""
--model <model> --tools "" --output-format json` (Claude Code 2.1.299), project ad0b16b. Same rubric; Defects is how
many of the case's injected defects the run named as a cause.

| Run | Root cause | Fix | Defects | Notes |
|---|---|---|---|---|
| sonnet-1 | yes | yes | 1 | Two status columns, the feed moved to `order_status` and newer rows carry `status` NULL (84 and 78 in the sample), the `case when` in `customers.sql` false for NULL, the 279.00 to confirm with a query; `coalesce(status, order_status) as status`, `dbt build`; `not_null` next to `accepted_values` and a completed-value reconciliation. |
| sonnet-2 | yes | yes | 1 | Same chain; `coalesce(order_status, status)`, a disagreement check and a query for the 279.00, `dbt build`; `not_null` on both status columns and a reconciliation of `customers` against `orders`. |
| sonnet-3 | yes | yes | 1 | Same, though it places the cut-over "from about late March" where the `orders` sample shows order 57 on 1 March with no status; `coalesce(status, order_status)`, `dbt build`; `not_null` and a reconciliation straight from the raw tables. |
| haiku-1 | yes | yes | 1 | Same chain, 824.00 matched to the 50 completed orders in `orders`, the 279.00 split "I have not verified"; `coalesce(order_status, status)` once a disagreement check returns 0, `dbt build --select stg_orders+`; `not_null` and a two-column consistency test. |
| haiku-2 | yes | yes | 1 | Same; `coalesce(order_status, status)` "If `disagree` is 0", `dbt build --select stg_orders+`; `not_null` on `stg_orders` and `orders`, and a note that the seed has no column tests. |
| haiku-3 | yes | yes | 1 | Same; `coalesce(order_status, status)` after a conflicting-rows check, an optional backfill of `status` from `order_status` at the source; `not_null` and a singular test that `stg_orders.status` equals `raw_orders.order_status`, 43 rows on the broken data. |
| opus-1 | yes | yes | 1 | The feed fills only `order_status` from about March, the 43 NULL rows holding the completed orders, `accepted_values` ignoring NULL; `coalesce(order_status, status)`, `dbt build`, 1103.00 expected after; `not_null` and a two-column consistency test. |
| opus-2 | yes | yes | 1 | Same; `coalesce(order_status, status)`, with `order_status as status` once the feed owner retires `status`, a query that `coalesce(order_status, status) = 'completed'` sums to 1103.00; `not_null` on both. |
| opus-3 | yes | yes | 1 | Same; `coalesce(order_status, status)`, `dbt build`; `not_null`, and a reconciliation test that also fails on any NULL status. |

**Verification.** Every fix matches a shape in the Verification paragraph above.

**What it says.** 9 of 9 on both counts, the same as the curated runs (Sonnet, Haiku and Opus 3 of 3 each). The standard sample carries the whole chain: `information_schema.columns` lists `order_status` on `raw_orders`, the `raw_orders` rows show 84 and 78 with `status` NULL and `order_status` filled beside older rows where the pair agrees, and `orders` by status shows the NULL bucket of 43 orders and 761.00 against 50 completed at 824.00. No run could see that the NULL bucket's completed share is 279.00, so each attached a confirmation query, and seven of nine put `order_status` first in the coalesce, which is the safer order since it is backfilled. The one slip is sonnet-3 placing the cut-over in "late March".
