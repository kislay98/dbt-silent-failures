# Score: utc_switch

**Runs:** `claude -p --setting-sources "" --model sonnet --tools "" --output-format json` and the same with `--model haiku` (Claude Code 2.1.296),
isolated from this machine's hooks and global instructions, all tools disabled, one turn each, prompt = `prompt.md`
(seeds hidden). Sonnet is `claude-sonnet-5-5` (26 s, USD 0.07), response in `response.md`. Haiku is `claude-haiku-5-5`
(43 s, USD 0.006), response in `response_haiku.md`.

## Claude Sonnet

**Root cause: found.** "`raw_orders.ordered_at` mixes two time zones: older orders in shop-local time (IST, UTC+5:30),
from about order 73 (mid-March) onward in UTC." It reconciled 26, 27 and 28 March and order 73 to the minute, said the
boundary needs confirming, and told the reader to check order 72 and its neighbours. The switch is at order 57
(1 March); orders 57 to 72 have shifted times but their dates did not move, so nothing in the prompt could place the
boundary earlier.

**Fix: partly.** `case when id >= 73 then ordered_at + 5h30 else ordered_at end`. Applied: every `order_date` matches
the clean data, and 16 timestamps (orders 57 to 72) stay 5h30 early. It also said to full-refresh `daily_revenue` and
to fix the feed. Its test, the app's daily counts loaded as a seed with a full outer join, fails with 9 days on the
broken data, passes after its fix and on clean data.

**Verdict:** pass on root cause, partial on fix: the dates ops complained about are right, 16 times of day are not, and
it flagged the boundary as unconfirmed.

## Claude Haiku

**Root cause: found.** The same mechanism and direction ("rows from around mid-March appear to be UTC, earlier rows
are shop-local"), the +5:30 recount of 26 to 28 March, and two things it said it could not verify: whether the feed or
the admin screen changed, and which rows are shifted. It asked about 23 and 24 March, which the prompt did not list and
which do disagree (4 to 5 and 2 to 1). It also noticed that AUD in the docs does not fit a +5:30 shop.

**Fix: partly.** The right fix in words (restate the post-cutover timestamps to local time at the source) with no
boundary, plus a staging change that needs an `ordered_at_tz` column the feed does not have, which it said. Not
applied. Its reconciliation test against the admin's daily counts fails with 9 days on the broken data and passes on
clean data; its unit test was not run.

**Verdict:** pass on root cause, partial on fix.

**Earlier runs (default config, confounded by the ponytail hook, 17 to 22 Oct):** Sonnet fail and fail (it explained the
offset as steady state, "the warehouse holds UTC", and its fix moved ten January and February orders a day forward);
Haiku not run. This is the one verdict that moved on the isolated re-run. One sample per run, so the persona cannot be
told from sampling noise; week 3 runs each case three times.
