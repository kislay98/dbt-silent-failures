# Score: null_payment_amounts

**Runs:** three per model, `claude -p --setting-sources "" --model <model> --tools "" --output-format json` (Claude Code 2.1.296),
isolated from this machine's hooks, tools off, prompt assembled by `check_case.py` with the seeds hidden and a models-and-tests
build log (`Log: models only`, a real `dbt build --exclude resource_type:seed`), project bef6b69; each run header carries the
sha256 of the prompt it answered. Sonnet is `claude-sonnet-5-5`, Haiku is `claude-haiku-5-5`; responses in `runs/`.
Scored on the week 4 rubric: root cause found yes/no (the run names the change that broke the data), complete fix yes/no (strict,
every affected value right after the fix).

| Run | Root cause | Fix | Notes |
|---|---|---|---|
| sonnet-1 | yes | yes | NULL amounts on the last payment row of each affected order, `sum` skips them, `daily_revenue` stale but right; back it up, re-ingest or patch the NULL rows from the processor, no `coalesce(amount, 0)`, then full-refresh. |
| sonnet-2 | yes | yes | Same; "the 4 rows above, plus any others that make up the 65.00". |
| sonnet-3 | yes | yes | Same; re-pull the missing amounts from the settlement, reload, then rebuild. |
| haiku-1 | yes | yes | Same; reload from the processor and "confirm the true cents for every NULL row"; `daily_revenue` to a table. |
| haiku-2 | yes | yes | Same; backfill every NULL payment from the settlement records, with the query that finds them all. |
| haiku-3 | yes | no | Names the mechanism and the source, but states that exactly four payments are NULL (the four in the sample) and fixes those four; the other four (orders 9, 18, 49, 51) stay short, and the 65.00 gap was never reconciled against its four. |

**Verification.** The amounts are not in the warehouse, so a complete fix is the reload of every NULL row. Restoring
the four visible rows leaves orders 18, 49 and 51 short; restoring all eight restores `orders` exactly.
`coalesce(amount, 0)` in staging changes nothing and hides the gap. `not_null` on `stg_payments.amount` fails with
8 rows on the broken data and passes clean; a settlement reconciliation fails with 1 row.

**What it says.** Six of six on the cause, five of six on the fix. The miss is scope: the sample showed four of the
eight NULL rows and one run took the sample for the population. Every run spotted that the dashboard agrees with the
settlement only because its history predates the NULLs.

## Opus runs (week 6)

Three runs of `claude-opus-5-5`, same command, prompt assembled by `check_case.py` from the project at f769fdf, before
the week 6 lookup extension, so the same project the other two models saw; verdicts in `results.csv`.

| Run | Root cause | Fix | Notes |
|---|---|---|---|
| opus-1 | yes | yes | NULL amounts on the second instalments, `daily_revenue` right by luck; reload the rows from the source, no `coalesce`, full refresh. |
| opus-2 | yes | yes | Same; adds hardening that makes an order's total NULL when any instalment has none, so `not_null` fires. |
| opus-3 | yes | yes | Same; full refresh only after the reload. |

Opus: 3 of 3 found, 3 of 3 fixed.

## Standard sample

**Runs:** three per model, week 8, on the standard output sample (the same queries on every case: row counts, the raw
column list, monthly totals of the output tables and ten rows of every table picked by hash); `runs/standard/prompt.md`
is the frozen prompt every run answered and `runs/standard/` holds the responses. `claude -p --setting-sources ""
--model <model> --tools "" --output-format json` (Claude Code 2.1.299), project ad0b16b. Same rubric; Defects is how
many of the case's injected defects the run named as a cause.

| Run | Root cause | Fix | Defects | Notes |
|---|---|---|---|---|
| sonnet-1 | yes | yes | 1 | NULL `amount` rows in `raw_payments`, two visible in the sample, `sum` skips them and `daily_revenue` agrees with the settlement only because its history predates the NULLs; list every NULL row, backfill the real amounts from the processor, no `coalesce(amount, 0)`, then full refresh. |
| sonnet-2 | yes | yes | 1 | Same; reload or backfill the missing amounts in cents from the settlement after a query that lists every NULL row by month, then `dbt build --full-refresh -s daily_revenue+ orders+`. |
| sonnet-3 | yes | yes | 1 | Same; list the NULL rows and reload them from the settlement, "The values can't be inferred from this data", leave `stg_payments.sql` as is so the `not_null` test stops the build. |
| haiku-1 | yes | yes | 1 | Same; reload the NULL `raw_payments.amount` values from the settlement and confirm "their processor amounts sum to 65.00", `daily_revenue` to a table. |
| haiku-2 | yes | no | 1 | Names the NULL amounts and the stale incremental, but takes the two sample rows for the population: "Two rows in `raw_payments` have a NULL `amount`" and the fix is "reload or patch `raw_payments` ids 30 and 55"; the other six stay short, and its `daily_revenue` to a table would then lose the only correct copy. |
| haiku-3 | yes | yes | 1 | Same cause, opened as "The likely root cause" with no alternative offered; restore the missing `raw_payments.amount` values from the settlement file, reject NULLs at ingestion, `daily_revenue` to a table with a one-time full refresh. |
| opus-1 | yes | yes | 1 | Same; find every NULL row, restore the amounts from the settlement or the source and re-run the load, with the per-day `daily_revenue` minus `orders` gap as a cross-check, then `dbt build --full-refresh` only after the data is fixed. |
| opus-2 | yes | yes | 1 | Same; re-run the ingestion for January to March or patch the rows from the settlement, "ids 30, 55 and the others", then rebuild, with a warning not to full-refresh `daily_revenue` first. |
| opus-3 | yes | yes | 1 | Same; find the damaged rows, reload them with their real amounts or correct `seeds/raw_payments.csv`, then `dbt build --full-refresh`. |

**Verification.** Every fix matches a shape in the Verification paragraph above.

**What it says.** Nine of nine on the cause and eight of nine on the fix, against six of six and five of six on the curated prompt (Opus three of three on both). The standard sample showed two of the eight NULL rows (payments 30 and 55 in `raw_payments`, 30 again in `stg_payments`), order 25 at 42.00 against the 58.00 settled, and the monthly `orders` and `daily_revenue` tables, and every run read the 38, 21 and 6 gaps as the 65.00, called the dashboard stale rather than right, and sent a query for the rows the sample did not show. The one miss is the same scope error as the curated haiku-3: haiku-2 patches "ids 30 and 55" only, leaving six rows short.
