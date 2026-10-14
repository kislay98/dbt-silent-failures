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

## Opus runs (week 6)

Three runs of `claude-opus-5-5`, same command, prompt assembled by `check_case.py` from the project at f769fdf, before
the week 6 lookup extension, so the same project the other two models saw; verdicts in `results.csv`.

| Run | Root cause | Fix | Notes |
|---|---|---|---|
| opus-1 | yes | yes | Every timestamp is UTC and the business day is India's; the sampled orders after 18:30 UTC reproduce every admin count; `at time zone` conversion of every row before the date, docs changed from Date (UTC), full refresh; a unit test on the 18:30 boundary. |
| opus-2 | yes | yes | Same; infers from the stale `daily_revenue` rows that the feed used to hold local time and the history was reloaded as UTC. |
| opus-3 | yes | yes | Same; lists the five admin-only days that have no UTC-side orders. |

Opus: 3 of 3 found, 3 of 3 fixed.

## Standard sample

**Runs:** three per model, week 8, on the standard output sample (the same queries on every case: row counts, the raw
column list, monthly totals of the output tables and ten rows of every table picked by hash); `runs/standard/prompt.md`
is the frozen prompt every run answered and `runs/standard/` holds the responses. `claude -p --setting-sources ""
--model <model> --tools "" --output-format json` (Claude Code 2.1.299), project ad0b16b. Same rubric; Defects is how
many of the case's injected defects the run named as a cause.

| Run | Root cause | Fix | Defects | Notes |
|---|---|---|---|---|
| sonnet-1 | yes | yes | 1 | `ordered_at` is UTC on every row, the cast takes the UTC day, the admin uses IST, order 73 reconciled to the minute from the `raw_orders` sample; `at time zone 'UTC'` then `'Asia/Kolkata'` before the date on every row, docs changed from Date (UTC), `dbt build --full-refresh -s stg_orders+`; a unit test on the 18:30 boundary and a `daily_revenue` versus `orders` reconciliation. |
| sonnet-2 | yes | yes | 1 | Same, with the 18:30 to 24:00 UTC band as the moved orders; `+ interval 330 minute` on every row, docs, `dbt build --full-refresh`; unit test with rows either side of 18:30. |
| sonnet-3 | yes | yes | 1 | Same; `at time zone` with the 330-minute interval as the fallback, `dbt build --full-refresh`; a singular test that `order_date` equals the local date of `raw_orders.ordered_at`. |
| haiku-1 | yes | yes | 1 | Same; `timezone('Asia/Kolkata', timezone('UTC', ordered_at))` before the date on every row with `ordered_at` kept in UTC, docs, `dbt build --select stg_orders+ --full-refresh`; warns ops that a last-day-of-month order could change month. |
| haiku-2 | yes | yes | 1 | Same; `+ interval '5 hours 30 minutes'` on every row, docs, `dbt build --full-refresh -s stg_orders+`, prefers IST in staging over a `daily_revenue`-only conversion; unit test on both sides of 18:30. |
| haiku-3 | yes | yes | 1 | Same; `at time zone` on every row, docs, `dbt build --select stg_orders+ --full-refresh`, with a local-day count from `raw_orders` to check against the admin screen. |
| opus-1 | yes | yes | 1 | Same, with "the feed or the staging model switched to the UTC `ordered_at` timestamp" as the likely history; `at time zone` with the zone as a var defaulting to Asia/Kolkata, docs, `dbt build --full-refresh`; notes that `delete+insert` keeps a day whose orders vanish. |
| opus-2 | yes | yes | 1 | Same, and infers from the stale `daily_revenue` rows that `ordered_at` "used to arrive as IST local time and the ingestion job now loads it as UTC", adds order 92 (20:55 UTC, 2 April IST); `at time zone` on every row, docs, `dbt build --full-refresh -s stg_orders+`, the two ops windows as the check. |
| opus-3 | yes | yes | 1 | Same, the ingestion job landing `ordered_at` in UTC named as most likely with a model change as the alternative, adds orders 10 and 92 from the sample; `at time zone` on every row, docs, `dbt build --full-refresh --select stg_orders+`. |

**Verification.** Every fix matches a shape in the Verification paragraph above.

**What it says.** Nine of nine found and fixed, the same as the nine curated runs. The standard sample carried the same anchor the curated query did, the `raw_orders` row for order 73 at `2018-03-15 19:45:20`, and every run read the 5h30 shift off it against the symptom text's 16 March 01:15; Opus-2 and Opus-3 also used orders 92 and 10 from the same sample, and all nine used the monthly `orders` and `daily_revenue` tables agreeing while the days differ to spot the stale incremental. No run considered a cut-over, because the symptom text puts disagreements in both January and March, so the blanket conversion that fails the `utc_switch` cases was the only shape proposed. As with the curated runs, most attribute the stale `daily_revenue` history to an earlier version of the model rather than a re-export of the feed; only Opus-2 says the ingestion job "now loads it as UTC", and Opus-3 and Opus-1 leave the feed and the model as alternatives.
