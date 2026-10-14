# Score: transfers_in_micros

**Runs:** three per model, `claude -p --setting-sources "" --model <model> --tools "" --output-format json` (Claude Code 2.1.296),
isolated from this machine's hooks, tools off, prompt assembled by `check_case.py` with the seeds hidden and a models-and-tests
build log (`Log: models only`, a real `dbt build --exclude resource_type:seed`), project bef6b69; each run header carries the
sha256 of the prompt it answered. Sonnet is `claude-sonnet-5-5`, Haiku is `claude-haiku-5-5`; responses in `runs/`.
Scored on the week 4 rubric: root cause found yes/no (the run names the change that broke the data), complete fix yes/no (strict,
every affected value right after the fix).

| Run | Root cause | Fix | Notes |
|---|---|---|---|
| sonnet-1 | yes | yes | Bank transfers from payment 83 (order 73, 16 March) 10,000 times too large; calls the unit "1/10,000-dollar" (it is millionths), the factor and boundary are right; `update ... amount / 10000` from the first bad id, full refresh. |
| sonnet-2 | yes | yes | Millionths of a dollar from payment 83; staging rule `amount / 1000000.0` for those rows; full refresh. |
| sonnet-3 | yes | yes | Micro-units from payment 83; `update` guarded by `amount >= 10000`; plausibility test. |
| haiku-1 | yes | yes | 10,000x on bank transfers from about payment 83; reconciles March and April to the statement; correct the raw rows at the source after confirming scope. |
| haiku-2 | no | yes | Reads the inflation as "not a constant factor" (190,000 and 260,000 "imply different factors"; they are 19.00 and 26.00 times 10,000) and leaves 114.00 "unexplained"; its fix, re-ingesting every raw row over 100,000 from the processor, still covers all ten rows. |
| haiku-3 | yes | yes | 10,000x from 16 March, micros implied by 3,000,000 for 3.00; ingestion fix plus an interim `update` for confirmed ids. |

**Verification.** Dividing the bank transfers from 15 March on (by order date, by payment id 83 and later, by a
1000.00 threshold, or by raw amount over 100,000) by 10,000 restores `orders` and `region_revenue`; dividing every
bank transfer shrinks the 23 earlier ones. A payment ceiling test fails with 10 rows on the broken data and passes
clean; so does the settlement total.

**What it says.** Five of six found, six of six fixed. The one miss is arithmetic: Haiku-2 did not divide 190,000 by
19 and concluded the factors differed, then recommended a reload that happens to cover every affected row.

## Opus runs (week 6)

Three runs of `claude-opus-5-5`, same command, prompt assembled by `check_case.py` from the project at f769fdf, before
the week 6 lookup extension, so the same project the other two models saw; verdicts in `results.csv`.

| Run | Root cause | Fix | Notes |
|---|---|---|---|
| opus-1 | yes | yes | Bank transfers in millionths from payment 83 (order 73, 16 March), reconciled 48 + 114 = 162 and 390,000 / 10,000 = 39; divide the rows from id 83 by 10,000 at the source after confirming the boundary, an explicit cut-over in staging as the stopgap, full refresh; a payment ceiling test. |
| opus-2 | yes | yes | Same; rejects a size heuristic because a 100.00 payment in cents is also a multiple of 10,000. |
| opus-3 | yes | yes | Same; the cut-over id as a var. |

Opus: 3 of 3 found, 3 of 3 fixed.

## Standard sample

**Runs:** three per model, week 8, on the standard output sample (the same queries on every case: row counts, the raw
column list, monthly totals of the output tables and ten rows of every table picked by hash); `runs/standard/prompt.md`
is the frozen prompt every run answered and `runs/standard/` holds the responses. `claude -p --setting-sources ""
--model <model> --tools "" --output-format json` (Claude Code 2.1.299), project ad0b16b. Same rubric; Defects is how
many of the case's injected defects the run named as a cause.

| Run | Root cause | Fix | Defects | Notes |
|---|---|---|---|---|
| sonnet-1 | yes | yes | 1 | Bank transfers in `raw_payments` from mid-March in micro-dollars, 10,000 times too large after the `/ 100` in `stg_payments.sql`, first bad id to be confirmed from `amount >= 1000000`; reload those rows in cents (`amount / 10000`) or a staging `case` from that id dividing by 1,000,000, then `dbt build --full-refresh -s stg_payments+`. |
| sonnet-2 | yes | yes | 1 | Micro-dollars on `bank_transfer` rows from mid-March, decodes 15,000,000 and 14,000,000 to 15.00 and 14.00; divide the rows from the cutoff by 10,000 at the source or a staging `case` on `ordered_at` dividing by 1,000,000, full refresh (its selector `-s daily_revenue+ orders+` skips `stg_payments`, `customers` and `region_revenue`, not scored). |
| sonnet-3 | yes | yes | 1 | 10,000x on `bank_transfer` rows after order 67, works out 48 correct plus 114 scaled from the March figure; divide the rows found by `amount >= 1000000` by 10,000 in `raw_payments.csv` after confirming they run from one id, `dbt seed && dbt build --full-refresh`, same threshold in the staging stopgap. |
| haiku-1 | yes | yes | 1 | `raw_payments.amount` on bank transfers from about 15 to 23 March 10,000 times too large, ties the March excess 1,139,886 to the `orders` versus `daily_revenue` gap; correct the affected raw rows or re-ingest at the right scale after confirming against the statement, refuses a divisor in staging, `dbt build --full-refresh -s daily_revenue` (listed before the nightly build that rebuilds `orders`). |
| haiku-2 | yes | yes | 1 | 10,000x on `bank_transfer` rows from the second half of March, this time with the arithmetic right (162 = 114 + 48); `update raw_payments set amount = amount / 10000` where `amount >= 1000000 and amount % 10000 = 0`, which catches all ten and no earlier row, then `dbt build --full-refresh -s daily_revenue` followed by `dbt build` (in that order, so the refresh reads the stale `orders`). |
| haiku-3 | yes | yes | 1 | `raw_payments.amount` on bank transfers off by 10,000x from mid-March, "could be two unit conversions or a changed feed unit", the ANZ feed named; restate the rows with raw `amount > 100000` from the statement and reload, no divisor in the model, `daily_revenue` to a table or `dbt build --full-refresh`. |
| opus-1 | yes | yes | 1 | Bank transfers from mid-March 10,000 times too large, reconciles 48 + 114 x 10,000 and says raw 15,000,000 should be 1,500, but calls the unit "1/10,000 of a dollar"; scored on its preferred fix, the ten rows reloaded in cents, which is right; its staging stopgap from the cutover id is "then amount / 10000" in place of the `/ 100`, which would leave order 73 at 300.00. |
| opus-2 | yes | no | 1 | Headline says the rows are "100× too large" and "arrive in ten-thousandths of a dollar" while its table shows raw 3,000,000 for 3.00 and the arithmetic says 10,000; the `update raw_payments` from the first bad id is "set amount = amount / 100", which leaves order 73 at 300.00. |
| opus-3 | yes | yes | 1 | Micro-units on `bank_transfer` rows from mid-March, ANZ feed unit change, 48 + 114 x 10,000 reconciled; reload in cents at the source (each row divided by 10,000) with a staging stopgap from a var cut-over id dividing by 1,000,000, `dbt build --full-refresh`. |

**Verification.** `amount >= 1000000` (sonnet-3, haiku-2) selects exactly the ten micro-unit rows on the broken data (checked against the injected seed; no other row is above 10,000). opus-1 is scored on its preferred fix, the ten rows reloaded in cents, which restores the data; its staging stopgap divides by 10,000 in place of 100 and would leave them 100 times too large. opus-2's `amount / 100` at the source is its only fix and leaves them 100 times too large: no. Every other fix matches a shape above.

**What it says.** Nine of nine found against eight of nine curated, and this time Haiku-2 did the division (114 x 10,000 + 48) that its curated run fumbled; eight of nine fixed against nine of nine. Every run used the monthly `orders` table (1,140,048 and 390,000), the raw sample rows 105 and 111 at 15,000,000 and 14,000,000 beside 200, 400 and 2,000, and the `orders` rows 78, 90 and 98, and most used the 622 versus 1,140,508 March gap to spot the stale incremental; none needed a hand-picked query. The one miss is arithmetic on the standard sample's raw values rather than diagnosis: Opus-2 says the rows are "100× too large" and updates the raw table with "set amount = amount / 100", which leaves the ten rows 100 times too large; Opus-1 makes the same slip in a stopgap beside a correct reload in cents, and Sonnet-2's rebuild selector skips the `stg_payments` it edits, neither of which is scored.
