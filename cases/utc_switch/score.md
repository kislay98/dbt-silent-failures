# Score: utc_switch

**Runs:** `claude -p --setting-sources "" --model sonnet --tools "" --output-format json` and the same with `--model haiku` (Claude Code 2.1.296),
isolated from this machine's hooks and global instructions, all tools disabled, one turn each, prompt = `prompt.md`
(seeds hidden). Sonnet is `claude-sonnet-5-5` (26 s, USD 0.07), response in `runs/sonnet-1.md`. Haiku is `claude-haiku-5-5`
(43 s, USD 0.006), response in `runs/haiku-1.md`.
Scored on the week 4 rubric: root cause found yes/no (the run names the change that broke the data), complete fix yes/no (strict, every affected value right after the fix).

## Claude Sonnet

**Root cause: found.** "`raw_orders.ordered_at` mixes two time zones: older orders in shop-local time (IST, UTC+5:30),
from about order 73 (mid-March) onward in UTC." It reconciled 26, 27 and 28 March and order 73 to the minute, said the
boundary needs confirming, and told the reader to check order 72 and its neighbours. The switch is at order 57
(1 March); orders 57 to 72 have shifted times but their dates did not move, so nothing in the prompt could place the
boundary earlier.

**Fix: no (strict).** `case when id >= 73 then ordered_at + 5h30 else ordered_at end`. Applied: every `order_date` matches
the clean data, and 16 timestamps (orders 57 to 72) stay 5h30 early. It also said to full-refresh `daily_revenue` and
to fix the feed. Its test, the app's daily counts loaded as a seed with a full outer join, fails with 9 days on the
broken data, passes after its fix and on clean data.

**Verdict:** root cause yes, fix no: the dates ops complained about are right, 16 times of day are not, and
it flagged the boundary as unconfirmed.

## Claude Haiku

**Root cause: found.** The same mechanism and direction ("rows from around mid-March appear to be UTC, earlier rows
are shop-local"), the +5:30 recount of 26 to 28 March, and two things it said it could not verify: whether the feed or
the admin screen changed, and which rows are shifted. It asked about 23 and 24 March, which the prompt did not list and
which do disagree (4 to 5 and 2 to 1). It also noticed that AUD in the docs does not fit a +5:30 shop.

**Fix: no (strict).** The right fix in words (restate the post-cutover timestamps to local time at the source) with no
boundary, plus a staging change that needs an `ordered_at_tz` column the feed does not have, which it said. Not
applied. Its reconciliation test against the admin's daily counts fails with 9 days on the broken data and passes on
clean data; its unit test was not run.

**Verdict:** root cause yes, fix no.

**Earlier runs (default config, confounded by the ponytail hook, 17 to 22 Oct):** Sonnet fail and fail (it explained the
offset as steady state, "the warehouse holds UTC", and its fix moved ten January and February orders a day forward);
Haiku not run. This is the one verdict that moved on the isolated re-run. One sample per run, so the persona cannot be
told from sampling noise; week 3 runs each case three times.

## Repeat runs

Two more runs per model, same command, prompt assembled by `check_case.py` from the week 3 working tree.

| Run | Root cause | Fix | Notes |
|---|---|---|---|
| sonnet-2 | yes | no | Mixed conventions, switch at about order 73; `case` on an order-id var: every date right, 16 timestamps still shifted. |
| sonnet-3 | yes | no | Same, with a unit test pinning order 73. |
| haiku-2 | yes | no | Switch around 15 March; `case` on the timestamp: same partial result. |
| haiku-3 | yes | no | Mixed conventions; normalise the pre-cut-over rows at the source then convert; the 15 March boundary gives the same partial result. |

All six runs now place the switch; none can place it before 15 March from the prompt, so every fix leaves the 1 to 14 March times of day 5h30 early while every `order_date` comes out right. Under the strict rubric that is 6 of 6 found, 0 of 6 fixed.

## Opus runs (week 6)

Three runs of `claude-opus-5-5`, same command, prompt assembled by `check_case.py` from the project at f769fdf, before
the week 6 lookup extension, so the same project the other two models saw; verdicts in `results.csv`.

| Run | Root cause | Fix | Notes |
|---|---|---|---|
| opus-1 | yes | no | Mixed conventions, shop-local until mid-March and 5h30 behind from about order 73; reconciles 26 to 28 March; says to check 23 and 24 March to place the switch between 73 and 84. Fix: an offset-aware parse, or a branch on `order_id >= 73` if the new rows are plain UTC: every date right, orders 57 to 72 still 5h30 early. |
| opus-2 | yes | no | Same switch at about order 73, keyed on an id var; same partial result; a daily-count reconciliation against an app export as the test. |
| opus-3 | yes | no | Third attempt after two hung calls. Same switch at order 73, with the older rows converted local-to-UTC and every date derived in shop time: every date right, and orders 57 to 72 shifted a second time; asks ops to check 23 and 24 March, which would place the switch earlier. |

Opus: 3 of 3 found, 0 of 3 fixed. The cut-over rule holds for the larger model too: it places the switch at order 73 like every other run, so every date comes out right and the sixteen times of day before 16 March stay 5h30 early.

## Standard sample

**Runs:** three per model, week 8, on the standard output sample (the same queries on every case: row counts, the raw
column list, monthly totals of the output tables and ten rows of every table picked by hash); `runs/standard/prompt.md`
is the frozen prompt every run answered and `runs/standard/` holds the responses. `claude -p --setting-sources ""
--model <model> --tools "" --output-format json` (Claude Code 2.1.299), project ad0b16b. Same rubric; Defects is how
many of the case's injected defects the run named as a cause.

| Run | Root cause | Fix | Defects | Notes |
|---|---|---|---|---|
| sonnet-1 | yes | no | 1 | Mixed conventions, shop-local until mid-March and UTC "from about 15 March onward", order 88 reconciled; `case` on a timestamp var set to 2018-03-15 adding 330 minutes: every date right, orders 57 to 72 still 5h30 early, and the rebuild `dbt build --full-refresh -s daily_revenue+` leaves the edited `stg_orders` out. |
| sonnet-2 | yes | no | 1 | Mixed conventions, local then UTC "from about order 71", with order 28 at 22:50 on 29 January as the proof that January is local; `case` on an id var set to 71: every date right, orders 57 to 70 still early, and `dbt build --full-refresh -s daily_revenue+` leaves `stg_orders` out. |
| sonnet-3 | yes | no | 1 | Local before mid-March, UTC "from roughly order 73 onward"; `case` on an id var defaulting to 73 with `at time zone`: every date right, orders 57 to 72 still early; `dbt build --full-refresh -s stg_orders+`. |
| haiku-1 | yes | no | 1 | Two zones in `ordered_at`, shop-local before and UTC from "on or before 15 March 2018 19:45"; the pre-cut-over rows converted local to UTC at the source and `order_date` derived in shop time from UTC on every row, so `stg_orders.ordered_at` ends up UTC everywhere and the January and February times move; cut-over unconfirmed. |
| haiku-2 | no | no | 0 | The model casts a UTC timestamp without converting to local, with the mid-March onset handed back: "something changed around that date, most likely in how `raw_orders.ordered_at` is written (a source change, a re-export, or a seed reload that mixed zones)" and "you need to resolve them before choosing a fix"; every row converted UTC to local in staging, which moves January and February dates. |
| haiku-3 | no | no | 0 | The project "treats it as UTC" and never converts, with the switch filed under what it "could not settle": "The likely explanation is that the feed changed how it writes `ordered_at` around 15 March, for example from local time to UTC"; the fix then converts every row UTC to local anyway, which moves January and February dates. |
| opus-1 | yes | no | 1 | Mixed conventions, 5h30 behind "from some cutover N (at or before 73) onward", order 88 reconciled, a lag and hour-of-day query plus an admin check to place N; the seed rows or a staging `case` adding 5h30 from `var('orders_tz_cutover_id')`, which is never given a value, then a full rebuild: right shape, no boundary. |
| opus-2 | yes | no | 1 | Local until mid-March, then "a clock that is 5h30m behind local time" from about order 73, doubting it is UTC given AUD; a re-export from the cut-over preferred, else `case when id >= var('orders_offset_from_id') then ordered_at + interval 330 minute` with the id left as "<confirmed id>" to be found by a lag query and the admin screen, then a full rebuild: right shape, no boundary. |
| opus-3 | yes | no | 1 | The feed "stopped being written in shop-local time around mid-March", most likely with a +05:30 suffix that DuckDB normalises to UTC on load, naive UTC as the other possibility; `ordered_at` loaded as varchar and converted only where a regexp finds an offset suffix, which no row in a seed of naive values has, so nothing changes, the naive-UTC fallback is a conditional "convert rows from the cutover id onwards" with no id, and `dbt build --full-refresh -s daily_revenue+` leaves `stg_orders` out. |

**Verification.** opus-3's conversion only where a regexp finds a `+HH:MM` or `Z` suffix is a no-op, since the feed writes plain timestamps and no row in the seed carries a suffix; every other fix matches a shape above, and as on the curated prompt none pins the conversion to the rows from 1 March.

**What it says.** Seven of nine found and none fixed, against nine found and none fixed on the curated prompt: Sonnet and Opus hold at 3 found, Haiku falls from 3 to 1. The standard sample carried the same evidence the curated prompt did (order 73 and the 26 to 28 March counts in the symptom text) plus order 88 at 2018-03-26 21:51:20, which sonnet-1 and opus-1 used, and raw order 28 at 22:50:40 on 29 January, which sonnet-2 used to prove January is local; the 1, 10 and 11 March `stg_orders` rows, whose dates did not move, told nobody anything, so every run that found the switch placed it at 15 March or order 71 to 73 as the curated runs did, and the two Opus runs that left the cut-over as an unset var gave a lag query and an admin check that could find it but no value. Haiku's two misses told the steady-state story, the warehouse holds UTC and `stg_orders` never converted, treated the mid-March onset as unsettled ("you need to resolve them before choosing a fix"), and converted every row. Three runs that edited `stg_orders.sql` prescribed `dbt build --full-refresh -s daily_revenue+`, which never rebuilds it, and opus-3 built on an offset-suffix theory whose regexp matches no row.
