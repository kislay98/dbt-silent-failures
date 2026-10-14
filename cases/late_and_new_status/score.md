# Score: late_and_new_status

**Runs:** three per model, `claude -p --setting-sources "" --model <model> --tools "" --output-format json` (Claude Code 2.1.296),
isolated from this machine's hooks, tools off, prompt assembled by `check_case.py` with the seeds hidden and a models-and-tests
build log (`Log: models only`, a real `dbt build --exclude resource_type:seed`), project f769fdf plus the week 6 lookup
extension (committed with week 6); each run header carries the sha256 of the prompt it answered. Sonnet is `claude-sonnet-5-5`,
Haiku is `claude-haiku-5-5`, Opus is `claude-opus-5-5`; responses in `runs/`.
Scored on the week 4 rubric: root cause found yes/no (the run names the change that broke the data), complete fix yes/no (strict,
every affected value right after the fix).

| Run | Root cause | Fix | Notes |
|---|---|---|---|
| sonnet-1 | yes | yes | Both: `delivered` is what the admin counts as completed (967 + 65 + 136 = 1168) and the six backfilled February orders sit outside the three-day window; `delivered` mapped to completed in staging, the widened lists restored, `daily_revenue` as a table with a full refresh. |
| sonnet-2 | yes | yes | Both; an `is_completed` flag in staging that `customers` and `orders` use, `daily_revenue` as a table, one rebuild; a `not_null` test on the flag so a new status cannot be accepted without a decision. |
| sonnet-3 | yes | yes | Both; `is_completed` plus a table, full refresh; a status-classification seed with a `relationships` test proposed as the guard. |
| haiku-1 | yes | yes | Both; `daily_revenue` as a table, `status in ('completed', 'delivered')` in `customers`, the description updated. |
| haiku-2 | yes | yes | Both; same shape, with a unit test that records the decision that `delivered` counts. |
| haiku-3 | yes | yes | Both; same shape; rules out the lookup fan-out from the matching monthly totals. |
| opus-1 | yes | yes | Both: `delivered` is what the admin counts as completed and the six back-dated orders sit outside the window; `delivered` mapped in staging with the lists restored, `daily_revenue` rebuilding any day whose totals no longer reconcile (or a table), full refresh. |
| opus-2 | yes | yes | Both, read as one change with two effects; an `is_completed` flag in staging used by `customers` and exposed on `orders`, `daily_revenue` as a table, full refresh. |
| opus-3 | yes | yes | Both; mapping in staging, lists restored, `daily_revenue` as a table since the raw tables carry no load timestamp. |

**Verification.** `delivered` counted as completed (mapped in staging or the filter widened in `customers`) with
`daily_revenue` rebuilt from `orders` gives the expected output: 105 orders, 1737.00 in both tables, completed
revenue 1168.00. The rebuild alone leaves completed revenue at 967.00; the status change alone leaves `daily_revenue`
at 99 orders and 1672.00; restoring the seeds removes the six real orders. The per-day reconciliation fails with 6 rows
on the broken data and passes clean.

**What it says.** Nine of nine on both counts: with both symptoms reported, the two defects are handled side by side, and
eight of nine runs made `daily_revenue` a plain table rather than widening the window (one Opus run rebuilds any day whose
totals no longer reconcile, which also works).

## Standard sample

**Runs:** three per model, week 8, on the standard output sample (the same queries on every case: row counts, the raw
column list, monthly totals of the output tables and ten rows of every table picked by hash); `runs/standard/prompt.md`
is the frozen prompt every run answered and `runs/standard/` holds the responses. `claude -p --setting-sources ""
--model <model> --tools "" --output-format json` (Claude Code 2.1.299), project ad0b16b. Same rubric; Defects is how
many of the case's injected defects the run named as a cause.

| Run | Root cause | Fix | Defects | Notes |
|---|---|---|---|---|
| sonnet-1 | yes | yes | 2 | Both: `delivered` is what the admin counts as completed (967 + 201 = 1168) and the six February orders were backfilled outside the three-day window; `status in ('completed', 'delivered')` in `customers` or an `is_completed` flag in `stg_orders`, `daily_revenue` full-refreshed and made a table. |
| sonnet-2 | yes | yes | 2 | Both: `delivered` is a newer source status for orders that used to arrive as `completed`, and the ingestion job backfilled February after `daily_revenue` had moved on; `delivered` mapped to `completed` in `stg_orders` with the widened lists restored, `daily_revenue` as a table, `dbt build --full-refresh`. |
| sonnet-3 | yes | yes | 2 | Both; an `is_completed` flag on `orders` (or the filter widened in `customers`), `daily_revenue` full-refreshed and made a table; the March 136.00 inferred from the arithmetic with a query to confirm it. |
| haiku-1 | yes | yes | 2 | Both: finance counts `delivered` as completed, and February orders backfilled into `raw_orders` after the table held April never enter the window; a `completed_statuses` var read by `customers`, `daily_revenue` as a table rebuilt once, recommended "pending finance sign-off". |
| haiku-2 | yes | no | 2 | Both named, but the status fix is held back: `daily_revenue` as a table with a full refresh is "the clear bug", while the `customers` filter change comes "only after finance confirms the definition. If `delivered` shouldn't count, the model is right and the admin's query needs fixing"; applied as written, completed revenue stays at 967.00. |
| haiku-3 | yes | yes | 2 | Both: `delivered` is new to the data and nothing classifies it, and February orders loaded after the table had advanced fall outside every window; `status in ('completed', 'delivered')` in `customers`, `daily_revenue` as a table, confirmation queries attached. |
| opus-1 | yes | yes | 2 | Both, with the split derived from the totals: 6 `delivered` orders in February (65.00, the backfill) and 8 in March (136.00); `delivered` mapped to `completed` in `stg_orders` with `source_status` kept and the lists restored to five values, `daily_revenue` full-refreshed and made a table. |
| opus-2 | yes | yes | 2 | Both: the shop started marking finished orders `delivered` and six February orders reached `raw_orders` after the table had passed early March; a `completed_statuses` var driving `is_completed` in `stg_orders`, used by `customers` and exposed on `orders`, `daily_revenue` full-refreshed and made a table; rules out late payments from the order count. |
| opus-3 | yes | yes | 2 | Both: a new `delivered` value the admin counts as completed, and February rows landing after February was built; notes the raw tables carry no `loaded_at` and guesses the change that introduced `delivered` also backfilled the six; an `order_statuses` seed joined in `stg_orders` for `is_completed`, `daily_revenue` full-refreshed and made a table. |

**Verification.** Every fix matches a shape in the Verification paragraph above.

**What it says.** Nine of nine named both defects and eight of nine fixed both, against nine of nine on both counts in the curated runs; the one miss is haiku-2, which called `daily_revenue` "the clear bug" and left the `customers` change to finance. The runs leaned on the status breakdown (completed 59 and 967.00, delivered 14 and 201.00) against the admin's 1168.00, the monthly `orders` versus `daily_revenue` tables for the February gap of 6 orders and 65.00, and the `stg_orders` row with order 75 `delivered` on 2018-03-18; the sample has no status-by-month table, so every run inferred the March 136.00 from arithmetic, and opus-1 went on to derive the 6 February and 8 March `delivered` orders from the totals. All eight complete fixes made `daily_revenue` a table, as in the curated set.
