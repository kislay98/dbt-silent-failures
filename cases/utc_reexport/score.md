# Score: utc_reexport

**Runs:** three per model, `claude -p --setting-sources "" --model <model> --tools "" --output-format json` (Claude Code 2.1.296),
isolated from this machine's hooks, tools off, prompt assembled by `check_case.py` with the seeds hidden and a models-and-tests
build log (`Log: models only`, a real `dbt build --exclude resource_type:seed`), project bef6b69; each run header carries the
sha256 of the prompt it answered. Sonnet is `claude-sonnet-5-5`, Haiku is `claude-haiku-5-5`; responses in `runs/`.
Scored on the week 4 rubric: root cause found yes/no (the run names the change that broke the data), complete fix yes/no (strict,
every affected value right after the fix).

| Run | Root cause | Fix | Notes |
|---|---|---|---|
| sonnet-1 | yes | yes | `ordered_at` is UTC on every row, the cast takes the UTC day, the admin uses IST; order 73 reconciled to the minute; convert every row (`timezone('Asia/Kolkata', timezone('UTC', ordered_at))`), full-refresh `daily_revenue`. |
| sonnet-2 | yes | yes | Same with a table of the five moved orders in the sample; `+ interval 330 minute`; full refresh. |
| sonnet-3 | yes | yes | Same; reproduces the admin counts for 23 to 28 March from the shift. |
| haiku-1 | yes | yes | Same; keeps `order_date_utc` and makes the local date the `order_date`; `daily_revenue` to a table. |
| haiku-2 | yes | yes | Same; names the five admin-only January days as the moved orders. |
| haiku-3 | yes | yes | Same; `+ interval 330 minute`, table, `dbt build --full-refresh`. |

**Verification.** Converting every row by 5h30 restores every date in `stg_orders`, `orders` and the day-by-day
aggregate; the `at time zone` form gives the same result. The `utc_switch` fix shape (convert from order 73 on)
leaves the January and February dates wrong. All six runs convert the date and leave `ordered_at` in UTC, which the
ground truth accepts as a uniform convention. A daily reconciliation to the admin counts fails on the broken data
and passes clean.

**What it says.** Six of six, the first timezone case either model fixes. The difference from `utc_switch` and
`utc_switch_blind` is that there is no boundary to find: every row is shifted, so the blanket conversion that broke
January in the switch cases is the right fix here. Every run still attributed the dashboard's agreement with the
admin to an earlier version of the model rather than to a re-export of the feed, which the prompt leaves open.
