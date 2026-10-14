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
