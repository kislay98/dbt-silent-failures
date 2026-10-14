# Score: utc_switch_blind

**Runs:** three per model, `claude -p --setting-sources "" --model <model> --tools "" --output-format json` (Claude Code 2.1.296),
isolated from this machine's hooks, tools off, prompt assembled by `check_case.py` with the seeds hidden, project
7d5b525 plus the week 3 working tree. Sonnet is `claude-sonnet-5-5`, Haiku is `claude-haiku-5-5`; responses in `runs/`.
Scored on the week 4 rubric: root cause found yes/no (the run names the change that broke the data), complete fix yes/no (strict, every affected value right after the fix).

| Run | Root cause | Fix | Notes |
|---|---|---|---|
| sonnet-1 | no | no | Mechanism and 5h30 right (India gives IST), the switch not: treated the feed as UTC throughout, offered the switch as an alternative with a query to decide, then wrote the blanket conversion (+330 minutes on every row). |
| sonnet-2 | no | no | Same mechanism, no switch; explained the mid-March start as coincidence ("earlier orders happened to fall before 18:30 UTC"), which is false, ten do. Blanket conversion. |
| sonnet-3 | yes | no | "The feed changed from IST to UTC around mid-March." `case` on a cut-over var defaulting to 15 March. |
| haiku-1 | no | no | Mechanism right, the switch not: steady-state UTC against IST, hedged on why it started mid-March; blanket conversion. |
| haiku-2 | yes | no | Placed the switch around 15 March, then applied the blanket conversion anyway, with a check that would have exposed the January orders. |
| haiku-3 | yes | no | Argued from the base rate (a quarter of orders after 18:30 UTC, none shifted before March) that early rows are local; two-step fix with the 15 March boundary. |

**Verification.** On this injection the blanket conversion moves ten January and February orders a day forward
(checked on `utc_switch`, same data). A boundary at 15 March, or at order 73, gets every `order_date` right and leaves
16 timestamps (1 to 14 March) 5h30 early, because none of those orders moved across midnight and nothing in the
prompt can place the switch earlier. The admin-count reconciliation every run proposed fails with 9 days on the broken
data and passes on clean data.

**What it says.** Without the example order the root-cause rate halves (3 of 6; three more named the zone mismatch but not the switch) and no fix is complete (0 of 6). The country gives the
offset; the only evidence for a switch is "the two always agreed" plus the absence of pre-March mismatches, and three
runs explained that away.

## Opus runs (week 6)

Three runs of `claude-opus-5-5`, same command, prompt assembled by `check_case.py` from the project at f769fdf, before
the week 6 lookup extension, so the same project the other two models saw; verdicts in `results.csv`.

| Run | Root cause | Fix | Notes |
|---|---|---|---|
| opus-1 | yes | no | IST wall-clock until mid-March, UTC after, with no example order to go on; every listed disagreement reproduced by +5:30; a cut-over id var "between the last order on 14 March and the first on the 15th", so every date comes right and the 1 to 14 March times stay 5h30 early; tells the reader to check the older rows are IST before applying. |
| opus-2 | yes | no | Same switch at about 15 March, keyed on a cut-over id; same partial result; order 89 at 15 minutes past the boundary cited as the proof. |
| opus-3 | yes | no | Same; places the first UTC row at order 73 from the counts and converts from there; same partial result. |

Opus: 3 of 3 found, 0 of 3 fixed.

## Standard sample

**Runs:** three per model, week 8, on the standard output sample (the same queries on every case: row counts, the raw
column list, monthly totals of the output tables and ten rows of every table picked by hash); `runs/standard/prompt.md`
is the frozen prompt every run answered and `runs/standard/` holds the responses. `claude -p --setting-sources ""
--model <model> --tools "" --output-format json` (Claude Code 2.1.299), project ad0b16b. Same rubric; Defects is how
many of the case's injected defects the run named as a cause.

| Run | Root cause | Fix | Defects | Notes |
|---|---|---|---|---|
| sonnet-1 | yes | no | 1 | Feed "in UTC from mid-March on", order 28 at 22:50 in January as the reason the early rows are local, with a check to run first; `case` on a `utc_feed_start` var defaulting to 15 March that converts `order_date` only and leaves `ordered_at` raw, so the 1 to 14 March rows and every timestamp stay wrong; the blanket conversion kept as the fallback "If the whole feed turns out to be UTC". |
| sonnet-2 | no | no | 0 | Zone mismatch right (UTC cast against an IST admin screen), the switch not: "I can't tell why it only started in mid-March. Either early rows happened to have no late-evening orders, or the feed switched from local time to UTC around then", with order 28 at 22:50 in its own sample; blanket `+ interval 330 minute` on every row. |
| sonnet-3 | no | no | 0 | Same mechanism, saw that order 28 "would already have been off by a day" and then explained it away: "the Jan–Feb rows are stored in local time and the feed switched to UTC around mid-March, or that nobody reconciled those earlier days"; blanket `at time zone 'Asia/Kolkata'` conversion, the cutover only as an aside. |
| haiku-1 | yes | no | 1 | Named the switch as its conclusion ("`raw_orders.ordered_at` changed convention around 2018-03-15. Earlier rows were India local time") with a query to confirm it, then recommended the blanket conversion anyway: "This assumes all `ordered_at` values are UTC. If the pre-cutover rows are already local, you need the cutover"; the `case` version has a `<cutover>` placeholder. |
| haiku-2 | yes | no | 1 | Switch named outright, order 28 cited as proof January is local, cutover placed "between 1 and 15 March"; `case` on a `<CUTOVER>` placeholder found by a query that returns order 73, converting `order_date` only, so the 1 to 14 March rows and every `ordered_at` stay 5h30 early. |
| haiku-3 | yes | no | 1 | Mixed IST-then-UTC feed, cutover "on or before 2018-03-15"; `case` with a hardcoded 15 March boundary on `order_date` only, same partial result; full refresh of `daily_revenue` with a selector that skips `stg_orders` and `orders`, noted not penalised. |
| opus-1 | yes | no | 1 | IST until mid-March then UTC, orders 28 and 50 as proof the early rows are local; shifts `ordered_at` and `order_date` from an `orders_utc_cutover_id` var found by the first evening order the admin screen disputes, which is 73, so the 1 to 14 March timestamps stay 5h30 early. |
| opus-2 | yes | no | 1 | Same switch "since around 12–15 March", order 92 as the clearest case; same id-keyed fix with the var set to 73 ("replace with the confirmed first UTC order id"), same partial result. |
| opus-3 | no | no | 0 | No answer: with tools off the run spent its whole response on some forty unexecuted Bash calls hunting for `raw_orders.csv` on disk and ended at "echo final check" without a diagnosis or a fix. |

**Verification.** Every fix matches a shape in the Verification paragraph above.

**What it says.** Same tally as the curated runs, 6 of 9 found (sonnet 1, haiku 3, opus 2 against 1, 2, 3) and 0 of 9 fixed, with every boundary again at 15 March or order 73 and six of the eight fixes converting `order_date` only, leaving `stg_orders.ordered_at` raw for all 43 rows. The random sample did the curated prompt's work by luck: orders 73 (15 March 19:45) and 92 (1 April 20:55) sit in the `raw_orders`, `stg_orders` and `orders` samples against admin days that do not exist, and order 28 (29 January 22:50) or 50 (23 February 18:53) in `raw_orders` let five runs argue the early rows are local; the `daily_revenue` sample, which still carries the clean 2 orders on 3 April where `orders` has 1, went unused by every run. Nothing in the sample places the cut-over at 1 March, since no 1 to 14 March order in it has an evening UTC time (order 58 at 17:13 on 1 March is the latest), so no run could do better than the 15 March boundary. The three misses were the two Sonnet runs that explained order 28 away ("Either early rows happened to have no late-evening orders"; "or that nobody reconciled those earlier days") and opus-3, which with tools off wrote some forty unexecuted Bash calls looking for the seed and never answered.
