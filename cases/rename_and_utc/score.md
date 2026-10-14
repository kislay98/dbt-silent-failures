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
gained one sentence ("until this load the two agreed on every day since January") and the case was run again; the
table above is the second set, and only it is in `results.csv`. The six first runs are kept in `runs/first_prompt/`
with the prompt they answered (sha256 `c5676332c543`), scored on the same rubric:

| Run | Root cause | Fix | Notes |
|---|---|---|---|
| sonnet-1 | no | no | The rename from order 57, then "raw timestamps are UTC" for every row: `coalesce(order_ts, ordered_at)` converted to a `shop_timezone` var, which moves the 56 backfilled orders too. |
| sonnet-2 | no | no | Same; "if you'd rather keep `order_date` in UTC, drop the wrapper, but then order 73 will not reconcile". |
| sonnet-3 | no | no | Same, "both are UTC"; warns that January and February may shift and asks ops to compare after the rebuild. |
| haiku-1 | no | no | Reads `order_ts` for every row and converts all of them, dropping `ordered_at`. |
| haiku-2 | no | no | `coalesce`, then the local conversion "as my recommendation" with UTC kept as an option; every row converted either way. |
| haiku-3 | no | no | Same; infers Asia/Kolkata from the offset and asks ops to confirm the zone. |

All six name the rename but read UTC as the feed's standing convention rather than the change, so under the rubric none
names the second defect, and every fix moves the 56 local rows. On the amended prompt the same two models are 5 of 6;
the one sentence is the difference, and the paper should say so.

**What it says.** Eight of nine on both counts, against 0 of 18 complete fixes on the two `utc_switch` cases: the rename
marks the cut-over (the rows with no `ordered_at` are the rows in UTC), so the mixed case is easier than its timezone
half alone. The one miss converted every row because it would not commit to which column was local.

## Standard sample

**Runs:** three per model, week 8, on the standard output sample (the same queries on every case: row counts, the raw
column list, monthly totals of the output tables and ten rows of every table picked by hash); `runs/standard/prompt.md`
is the frozen prompt every run answered and `runs/standard/` holds the responses. `claude -p --setting-sources ""
--model <model> --tools "" --output-format json` (Claude Code 2.1.299), project ad0b16b. Same rubric; Defects is how
many of the case's injected defects the run named as a cause.

| Run | Root cause | Fix | Defects | Notes |
|---|---|---|---|---|
| sonnet-1 | yes | no | 2 | The rename from March and `order_ts` in UTC "assuming UTC", with order 73 as the check it could not run ("I couldn't confirm this. Check it first"); `coalesce(ordered_at, timezone('Australia/Sydney', timezone('UTC', order_ts)))` on the NULL rows only, full refresh, a unit test pinning order 73 to 16 March. The zone is guessed from the state names (+11, +10 from 1 April) against the shop's +5h30, so all 43 converted timestamps differ from clean and orders with a UTC hour from 13:00 to 18:29 (77 and 65 in the sample) land a day late. |
| sonnet-2 | yes | no | 2 | The rename from the March load and "the new `order_ts` is very likely UTC, so the date can shift", order 73 as the check; `coalesce(ordered_at, timezone(var('shop_timezone', 'Australia/Sydney'), timezone('UTC', order_ts)))` on the NULL rows, `dbt build --full-refresh -s stg_orders+`, a singular test pinning order 73 to 16 March. Same wrong zone as sonnet-1, so as written the converted rows do not match clean. |
| sonnet-3 | no | no | 1 | The rename only; plain `coalesce(ordered_at, order_ts)` with a full refresh, the timezone left to a check after the rebuild: "If it comes out as 2018-03-15, the new column is UTC and must be converted". Seven orders a day early. |
| haiku-1 | no | no | 1 | The rename, and `order_ts` "uses the same clock as `ordered_at`" because the January and February pairs are equal; plain `coalesce(ordered_at, order_ts)`, UTC only as "If `order_ts` is UTC instead, convert it". Seven orders a day early, and its rebuild full-refreshes `daily_revenue` before `stg_orders` is rebuilt and then excludes it, so `daily_revenue` is built from the broken `orders`. |
| haiku-2 | no | no | 1 | The rename; two versions of `stg_orders`, plain coalesce if order 73 reads local and an `Australia/Sydney` conversion if it reads UTC, "Step 1: Confirm the clock of the new rows", neither recommended. The first is seven orders a day early, the second the wrong zone. |
| haiku-3 | no | no | 1 | The rename, the timezone a "Secondary issue to verify"; plain `coalesce(ordered_at, order_ts)`, "If it reads 2018-03-15 14:15:20, the value is UTC and the cast needs a timezone conversion", full refresh of `daily_revenue`. Seven orders a day early; adds a test that `ordered_at` equals `order_ts` where both are present, which never fires. |
| opus-1 | no | no | 1 | The rename; `coalesce(order_ts, ordered_at)` "If `order_ts` is local time (consistent with the overlapping Jan/Feb rows)" and an `Australia/Sydney` conversion "If `order_ts` is UTC for the new rows", settled by a query on order 73, full refresh. Neither chosen; the first is seven orders a day early, the second the wrong zone. |
| opus-2 | no | no | 1 | The rename; "Case A" plain coalesce if order 73 reads local, "Case B" `Australia/Sydney` if it reads UTC, full refresh, with the stale `daily_revenue` March and April rows kept as the reference to diff against. Neither chosen; the diff would expose both the naive coalesce and the Sydney offset, but the response does not pick. |
| opus-3 | no | no | 1 | The rename; `coalesce(order_ts, ordered_at)` with the comment "both hold the shop's local time", the `Australia/Sydney` conversion only "If the order 73 check shows `order_ts` is UTC", full refresh. Seven orders a day early; a `coalesce(order_ts, ordered_at) is not null` seed test and a unit test on order 73. |

**Verification.** sonnet-1 and sonnet-2 convert the right rows (`ordered_at` NULL) but from UTC to Australia/Sydney, +11h and +10h instead of the shop's +5h30: every converted timestamp differs from the clean data and nine of the 43 orders land a day late (checked against the injected seed: ids 58, 62, 65, 71, 77, 81, 86, 98, 99), so no. The plain coalesce of the other seven leaves the seven UTC rows a day early, as on the curated prompt.

**What it says.** 2 of 9 found both defects and 0 of 9 fixed, against 8 of 9 on both counts in the curated runs. All nine read the rename off the `raw_orders` sample (`ordered_at` NULL and `order_ts` filled on rows 63 to 96, equal pairs on the January and February rows) and tied the NULL month to the stale incremental `daily_revenue`, but the sample hides order 73's `order_ts`, so the 5h30 the curated runs wrote as `+ 330 minutes` is not derivable here; seven runs took the equal January and February pairs as proof that `order_ts` shares the clock, offered the UTC conversion only as a branch behind a query on order 73 ("If it returns 2018-03-16 01:15:20, order_ts is local time, like ordered_at"), and recommended plain coalesce. The two Sonnet runs that committed to UTC had no offset to use and guessed `Australia/Sydney` from the state names in `raw_customer_regions`, the wrong zone for +5h30 data, so even the right reading could not produce a matching fix. On the standard sample the second defect is detectable only from the symptom sentence about order 73, and its size not at all.
