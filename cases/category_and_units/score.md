# Score: category_and_units

**Runs:** three per model, `claude -p --setting-sources "" --model <model> --tools "" --output-format json` (Claude Code 2.1.296),
isolated from this machine's hooks, tools off, prompt assembled by `check_case.py` with the seeds hidden and a models-and-tests
build log (`Log: models only`, a real `dbt build --exclude resource_type:seed`), project 8ee5734 (week 6 committed, with the
lookup extension); each run header carries the sha256 of the prompt it answered. Sonnet is `claude-sonnet-5-5`, Haiku is
`claude-haiku-5-5`, Opus is `claude-opus-5-5`; responses in `runs/`.
Scored on the week 4 rubric: root cause found yes/no (the run names the change that broke the data), complete fix yes/no (strict,
every affected value right after the fix).

| Run | Root cause | Fix | Notes |
|---|---|---|---|
| sonnet-1 | no | no | `delivered` from 10 March behind the widened lists, mapped in staging with the lists restored; the 1.98 residue noticed and read as "a cents/dollars issue on small coupon payments" for order 76 only, "which I can't confirm", with a query for ingestion; the coupon fix not made. |
| sonnet-2 | yes | yes | Both: `delivered` never treated as completed, and coupon rows loaded in dollars on orders 76, 81, 86, 92, 94 and 95, with order 76 closing the gap to 136.00 exactly; an `is_completed` flag on `orders` used by `customers`, the six rows reloaded in cents at the source, `daily_revenue` full refresh. |
| sonnet-3 | yes | yes | Both, the second "likely" from the six sub-dollar coupons all on or after 20 March; `delivered` mapped in staging, lists restored, coupons reloaded in cents by the ingestion job, full refresh. |
| haiku-1 | no | no | `delivered` found and the filter widened in `customers`; the 1.98 "I can't explain from these samples", left to the admin's order list; a `unique` test on the lookup as a latent risk. |
| haiku-2 | no | no | "Can't name a single root cause yet": stale tables, late payments or admin rules, with queries to decide; never reads the `delivered` rows in the sample; dedupes the regions feed as a latent defect. |
| haiku-3 | no | no | Same shape: missing March payments or orphan payments, the `delivered` rows unread; a `relationships` test proposed. |
| opus-1 | yes | yes | Both, with the 136.00 reconciled to the cent (134.02 plus the 1.98 on order 76); `delivered` mapped in staging with the source value kept, lists restored, coupon rows backfilled times 100 from the first bad id, `dbt build --full-refresh`. |
| opus-2 | yes | yes | Both, with the six coupons tabulated against their real amounts; mapping in staging, lists restored, the rows found by `amount % 100 <> 0` and backfilled, full refresh; refuses a "coupon under 100 means dollars" heuristic in staging. |
| opus-3 | yes | yes | Both, with each customer's gap matched to one delivered order; mapping in staging, lists restored, `update raw_payments set amount = amount * 100 where payment_method = 'coupon' and amount < 100`, full refresh. |

**Verification.** `delivered` counted as completed (mapped in staging, or the filter in `customers` widened) with the six coupon rows times 100 (by id, from 15 March, or every coupon under 1.00, which the clean data never has) restores `customers`, `orders` and `region_revenue` to the cent. The status change alone leaves completed revenue at 1101.02; the coupon fix alone leaves it at 967.00. A reconciliation of completed revenue to the admin, a check for coupon payments under 1.00 and the original five-value `accepted_values` list each fail on the broken data and pass clean.

**What it says.** Five of nine found and fixed. The second defect sits behind the first as a 1.98 residue, and the runs split on whether to chase it: Opus three for three and two Sonnet runs read the six sub-dollar coupons in the listing and closed the 136.00 to the cent; one Sonnet run and one Haiku run saw the residue and left it unexplained; two Haiku runs never read the `delivered` rows at all and proposed stale tables or orphan payments, the first Haiku misses on a status change the sample shows (3 of 3 on `status_new_value`).
