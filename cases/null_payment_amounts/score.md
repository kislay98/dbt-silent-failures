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
