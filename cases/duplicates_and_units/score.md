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
