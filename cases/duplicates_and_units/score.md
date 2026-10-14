# Score: duplicates_and_units

**Runs:** three per model, `claude -p --setting-sources "" --model <model> --tools "" --output-format json` (Claude Code 2.1.296),
isolated from this machine's hooks, tools off, prompt assembled by `check_case.py` with the seeds hidden and a models-and-tests
build log (`Log: models only`, a real `dbt build --exclude resource_type:seed`), project f769fdf plus the week 6 lookup
extension (committed with week 6); each run header carries the sha256 of the prompt it answered. Sonnet is `claude-sonnet-5-5`,
Haiku is `claude-haiku-5-5`, Opus is `claude-opus-5-5`; responses in `runs/`.
Scored on the week 4 rubric: root cause found yes/no (the run names the change that broke the data), complete fix yes/no (strict,
every affected value right after the fix).

| Run | Root cause | Fix | Notes |
|---|---|---|---|
| sonnet-1 | yes | yes | Both: replayed rows from id 114 and gift cards in dollars, reconciled per method (980 - 109, 468 - 57, 2.19 x 100 - 14); staging dedupe on (`order_id`, `payment_method`, `amount`) keeping the first id plus a gift-card unit rule, full refresh. |
| sonnet-2 | yes | yes | Both; delete the replayed rows at the source, a `qualify` dedupe as the guard, the gift-card rule, full refresh; warns the dedupe key would also collapse a genuine identical split. |
| sonnet-3 | yes | yes | Both; staging dedupe plus the gift-card rule, full refresh; tells the reader to check that only ids 114 and up drop. |
| haiku-1 | no | no | The duplicates, then the gift card gap read as rows missing from `raw_payments` ("nothing in the project drops or rescales gift card rows"); the fix reloads rows that are not missing and leaves the twelve dollar rows at a hundredth. |
| haiku-2 | yes | yes | Both, the unit error flagged "likely, needs confirmation" with the 2.05 x 100 = 205.00 check; remove the copies at the source keeping the lowest id, gift-card rule, full refresh. |
| haiku-3 | yes | yes | Both; staging dedupe plus the gift-card rule, full refresh; asks for a unit column on the lookup instead of a hard-coded method. |
| opus-1 | yes | yes | Both: twelve replays under ids 114 to 125 (+109 cards, +57 transfers) and gift cards in dollars (219 raw minus the replayed 14 = 205); delete the copies and have the feed send cents, a dedupe plus unit rule in staging as the guard, full refresh. |
| opus-2 | yes | yes | Both; the same data fixes with a confirming query that every row above 113 has an original, defensive dedupe, full refresh. |
| opus-3 | yes | yes | Both; `delete ... between 114 and 125` and `amount * 100` on the gift card rows at the source, an interim staging guard with its caveats, full refresh. |

**Verification.** Deleting rows 114 to 125 and restoring cents on the twelve gift card rows, or a staging dedupe on
(`order_id`, `payment_method`, `amount`) plus the same unit rule, restores `orders`, `customers` and `region_revenue`
(the dedupe removes exactly the twelve copies; the replayed gift card row goes with them). The gift cards alone leave
`orders.amount` at 1852.00, the duplicates alone at 1469.05. The duplicate-key test fails with 12 rows on the broken
data, the sub-dollar check with 13 (the twelve rows and their copy), the gift card statement check with 1; all pass clean.

**What it says.** Eight of nine found and fixed. The one miss (haiku-1) is the direction error seen before on `coupon_cents` and `join_drops_rows`: a shortfall read as rows missing rather than values shrunk, with the 0.23 rows in the sample explained away. The two defects nearly cancel in the total (1635.19 against 1672.00), and every run that found both reconciled per method rather than in total.

## Standard sample

**Runs:** three per model, week 8, on the standard output sample (the same queries on every case: row counts, the raw
column list, monthly totals of the output tables and ten rows of every table picked by hash); `runs/standard/prompt.md`
is the frozen prompt every run answered and `runs/standard/` holds the responses. `claude -p --setting-sources ""
--model <model> --tools "" --output-format json` (Claude Code 2.1.299), project ad0b16b. Same rubric; Defects is how
many of the case's injected defects the run named as a cause.

| Run | Root cause | Fix | Defects | Notes |
|---|---|---|---|---|
| sonnet-1 | yes | yes | 2 | Both: re-loaded copies on (`order_id`, `payment_method`, `amount`) under new ids (125 rows, +109.00 cards, +57.00 transfers) and gift cards in dollars (order 9's 23 as 0.23; 219 raw less 14 of copies is 205.00); staging `row_number` dedupe keeping the first id plus the gift-card unit rule, `dbt build --full-refresh`, the confirming query attached and "if any order legitimately has two identical payments, partition by a source transaction ID instead" as a caveat only. |
| sonnet-2 | yes | yes | 2 | Both: gift cards in dollars (0.28 staged from a raw 28) and "the ingestion job loaded some payments twice, under new ids" (+109.00, +57.00, +14.00 gift); delete the later ids at the source from the candidate query, the unit rule in `stg_payments`, full refresh; "check the list above against the processor statement before deleting". |
| sonnet-3 | yes | yes | 2 | Both, with a per-method gap table summing to the 36.81; staging dedupe on (`order_id`, `payment_method`, `amount`) plus the unit rule, "I expect ids above 113", full refresh. Its rebuild selector `-s daily_revenue+ orders+` leaves out the `stg_payments` it edited; rebuild commands are not scored (several curated runs have the same slip), the fix shape is. |
| haiku-1 | yes | no | 2 | Both, the unit error "high confidence on the pattern" and the copies "probably by duplicate payment rows (medium-high confidence)" with the 36.81 check; the unit rule in staging and a table `daily_revenue`, but no removal of the loaded copies: "Dedupe only on the processor's transaction ID if the feed has one. If it does not, the ingestion job needs to dedupe on re-delivery", which leaves `orders.amount` at 1852.00. |
| haiku-2 | yes | yes | 2 | Both, the copies from order 1 and 125 rows for 99 orders, the unit error from "2.05 + 0.14 ≈ 2.19. So a few gift card rows appear to be recorded in dollars" (every gift card row is); `qualify` dedupe keeping the first id in staging, times 100 at the source on the rows its gift-card query lists, `dbt build -s daily_revenue --full-refresh` after the data is fixed. |
| haiku-3 | yes | yes | 2 | Both, the 14.00 residual left as "probably a duplicate or a redemption the statement excludes"; times 100 on the gift-card rows at the source and "remove duplicate card and bank rows only after matching them to processor transaction references", refusing the natural key, the candidates from its own query; the unit rule as a stopgap, `daily_revenue` as a table with a full refresh. |
| opus-1 | yes | yes | 2 | Both: twelve copies on (`order_id`, `payment_method`, `amount`) with new ids and gift cards in dollars (219 = 205 + 14 of copies), reconciled per method to 1672.00; the upstream fix plus a staging `qualify` dedupe and unit rule as the guard, `dbt build --full-refresh -s stg_payments+`. |
| opus-2 | yes | yes | 2 | Both, the same reconciliation with "219 − 205 = 14 of duplicates"; at the source, `delete` the later id of each pair from its query and `update ... amount * 100 where payment_method = 'gift_card'`, `dbt build --full-refresh`; staging patches rejected as the kind that "would silently break the day ingestion is fixed". |
| opus-3 | yes | yes | 2 | Both; staging `qualify` dedupe plus the unit rule as the guard until ingestion is fixed, the statement totals as the check and delete by id as the fallback, `dbt build --full-refresh -s daily_revenue` after the fix. |

**Verification.** sonnet-3's rebuild selector `-s daily_revenue+ orders+` skips the `stg_payments` it edited; rebuild commands are not scored beyond the full refresh, as on the curated runs, and its fix shape (staging dedupe plus the unit rule) is one verified above. Every other fix matches a shape above.

**What it says.** Nine of nine named both defects and eight fixed both, against eight and eight in the curated runs; the standard `raw_payments` sample shows no gift card row and no duplicate pair, so every run derived both from the complaint's per-method arithmetic (980.00 less 871.00, 468.00 less 411.00, 2.19 times 100 as 219 against 205.00) and the 125 payments for 99 orders, with the sub-dollar gift card rows in the `orders` and `stg_payments` samples (0.23, 0.06, 0.28) as the unit evidence. The one fix miss (haiku-1) found both and would not remove the copies without a transaction id ("the ingestion job needs to dedupe on re-delivery"), a different miss from the curated haiku-1's direction error; haiku-3 removes them only "after matching them to processor transaction references". All nine also read the `daily_revenue` month table (496.00 against 518.52) as a stale incremental and called for a full refresh, which this case needs.
