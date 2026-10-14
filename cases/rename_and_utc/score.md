# Score: rename_and_utc

**Runs:** three per model, `claude -p --setting-sources "" --model <model> --tools "" --output-format json` (Claude Code 2.1.296),
isolated from this machine's hooks, tools off, prompt assembled by `check_case.py` with the seeds hidden and a models-and-tests
build log (`Log: models only`, a real `dbt build --exclude resource_type:seed`), project f769fdf plus the week 6 lookup
extension (committed with week 6); each run header carries the sha256 of the prompt it answered. Sonnet is `claude-sonnet-5-5`,
Haiku is `claude-haiku-5-5`, Opus is `claude-opus-5-5`; responses in `runs/`.
Scored on the week 4 rubric: root cause found yes/no (the run names the change that broke the data), complete fix yes/no (strict,
every affected value right after the fix).

| Run | Root cause | Fix | Notes |
|---|---|---|---|
| sonnet-1 | yes | yes | The rename from order 57, and the new column in UTC while the old one was local "because it reconciled"; `coalesce(ordered_at, order_ts + 330 minutes)` for the timestamp and the date, full refresh; "do not use `order_ts` alone, the January and February values are already local". |
| sonnet-2 | yes | yes | Same; the 5h30 added only where `ordered_at` is NULL, docs changed from Date (UTC). |
| sonnet-3 | yes | yes | Same with `at time zone`; "only convert when `ordered_at` is NULL"; lists the expected monthly counts after the fix. |
| haiku-1 | yes | yes | Same; converts only the NULL rows "so January and February keep their current dates", a regression test pinning order 73 to 16 March. |
| haiku-2 | yes | yes | Same, in a `localised` CTE; explains why `delete+insert` cannot remove a day that vanished from the source. |
| haiku-3 | no | no | The rename and the offset, then "I don't know for certain which column is the UTC one"; reads `order_ts` for every row and converts all of them, which moves the 56 backfilled orders a day. |
| opus-1 | yes | yes | The rename from order 57, `order_ts` local for 1 to 56 "because orders matched the admin screen every day" and UTC from 57; `coalesce(ordered_at, order_ts + 5h30)`, "not order_ts for every row", full refresh; a unit test that a naive coalesce would fail. |
| opus-2 | yes | yes | Same, with a `shop_timezone` var and the customer 22 and 8 symptoms traced; asks ops to confirm the zone. |
| opus-3 | yes | yes | Same; keeps the stale `daily_revenue` as the reference to diff the rebuilt table against, which should return zero rows. |

**Verification.** `coalesce(ordered_at, order_ts + interval '5 hours 30 minutes')`, or the same shift applied to
`order_ts` from 1 March, restores `stg_orders`, `orders`, `customers` and the day-by-day aggregate exactly. `coalesce`
alone brings the months back with seven orders a day early (73, 82, 87, 88, 89, 92, 93); shifting every `order_ts`
moves the 56 backfilled orders instead. `not_null` on `stg_orders.ordered_at` fails with 43 rows on the broken data and
passes clean.

**Prompt note.** The first prompt said ops reconciles daily against the admin screen but not that the two had agreed
before this load; with the project docs calling `order_date` "Date (UTC)" and rows 54 to 56 carrying equal timestamps,
all six first runs read the whole history as UTC and converted every row. That reading was defensible, so the prompt
gained one sentence ("until this load the two agreed on every day since January"), the six runs were set aside, and the
case was run again; the table above is the second set.

**What it says.** Eight of nine on both counts, against 0 of 18 complete fixes on the two `utc_switch` cases: the rename
marks the cut-over (the rows with no `ordered_at` are the rows in UTC), so the mixed case is easier than its timezone
half alone. The one miss converted every row because it would not commit to which column was local.
