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
