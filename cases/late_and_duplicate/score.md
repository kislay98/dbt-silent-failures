# Score: late_and_duplicate

**Runs:** three per model, `claude -p --setting-sources "" --model <model> --tools "" --output-format json` (Claude Code 2.1.296),
isolated from this machine's hooks, tools off, prompt assembled by `check_case.py` with the seeds hidden, project
7d5b525 plus the week 3 working tree. Sonnet is `claude-sonnet-5-5`, Haiku is `claude-haiku-5-5`; responses in `runs/`.

| Run | Root cause | Fix | Notes |
|---|---|---|---|
| sonnet-1 | yes | yes | Both defects: replayed payments under fresh ids (131 rows for 105 orders, order 1, 180.00) and the daily table's window anchored to `max(order_date)`. Fix: remove the replayed rows keeping the lowest id, daily table as a table, full refresh; expects 1737.00 on both. |
| sonnet-2 | yes | yes | Both; one arithmetic slip (called the late-order gap 51 rather than 65, because the daily table already holds the 7 April replay) but the right end figure. |
| sonnet-3 | yes | yes | Both; same fix. |
| haiku-1 | yes | yes | Both named; kept "split tender the processor never settles" as an alternative for the 180.00 despite order 1, with a query that resolves it; conditional data fix, table, full refresh. |
| haiku-2 | yes | yes | Both; dedupe in staging on (`order_id`, `payment_method`, `amount`) plus the table. |
| haiku-3 | yes | yes | Both; data fix plus the table. |

**Verification.** Removing payments 120 to 131 with `daily_revenue` as a table, or with a full refresh, gives 105
orders and 1737.00 in both tables; the staging dedupe plus the table lands there too. The daily-against-orders test
fails with 16 rows and the duplicates test with 12 on the broken data; both pass on clean data (42/42). Every run
kept the six late orders, which is the trap this case sets.

**What it says.** Six of six on two overlapping failures; the per-day mismatch sample carried both signals.

## Opus runs (week 6)

Three runs of `claude-opus-5-5`, same command, prompt assembled by `check_case.py` from the project at f769fdf, before
the week 6 lookup extension, so the same project the other two models saw; verdicts in `results.csv`.

| Run | Root cause | Fix | Notes |
|---|---|---|---|
| opus-1 | yes | yes | Both: twelve replayed payments (+180) and the six backfilled February orders the window never sees (-51); remove the copies at the source from an (order, method, amount) listing, keep the late orders, `daily_revenue` as a table, full refresh. |
| opus-2 | yes | yes | Same, with the excess split by date to the cent. |
| opus-3 | yes | yes | Same. |

Opus: 3 of 3 found, 3 of 3 fixed.

## Standard sample

**Runs:** three per model, week 8, on the standard output sample (the same queries on every case: row counts, the raw
column list, monthly totals of the output tables and ten rows of every table picked by hash); `runs/standard/prompt.md`
is the frozen prompt every run answered and `runs/standard/` holds the responses. `claude -p --setting-sources ""
--model <model> --tools "" --output-format json` (Claude Code 2.1.299), project ad0b16b. Same rubric; Defects is how
many of the case's injected defects the run named as a cause.

| Run | Root cause | Fix | Defects | Notes |
|---|---|---|---|---|
| sonnet-1 | yes | yes | 2 | Both: payments re-delivered under new ids (payment 121 on order 10, order 1 at 20.00, 180.00) and the `daily_revenue` window anchored to `max(order_date)` missing the six backfilled February orders 100 to 105; fix is the staging dedupe `qualify row_number() over (partition by order_id, payment_method, amount order by id) = 1` gated on a query that would confirm the 180.00, `daily_revenue` as a table and a full refresh. Muddled the dashboard's direction, "The dashboard is stale on top of being inflated", without touching the fix. |
| sonnet-2 | yes | yes | 2 | Both: duplicates under new ids in `raw_payments.csv` and the stale `daily_revenue` missing the backfilled February orders (27 against 33), 51.00 below settlement; fix removes the later ids per duplicate group from the seed after reconciling to the settlement, `daily_revenue` as a table and a full refresh. Put "ids 114–131" together as the late load without separating the six real late payments, which its group-by query would do. |
| sonnet-3 | yes | yes | 2 | Both: reloaded payments with new ids (payment 121 on a January order) and the window never picking up the February backfill; fix deletes all but the lowest id per group from the seed after confirming against the processor, `daily_revenue` as a table and a full refresh. |
| haiku-1 | yes | yes | 2 | Both: duplicates under new ids against the 113-payment baseline and the February backfill missing from `daily_revenue` (99 against 105 orders), the 231 split as 100 + 74 + 57; fix removes the extra rows from the seed after the confirming query, `daily_revenue` as a table, `dbt build --full-refresh`, expecting 1737.00 and 105 orders. |
| haiku-2 | yes | yes | 2 | Both, with the per-month table; fix removes the duplicate rows from `raw_payments.csv` after checking processor ids, `daily_revenue` as a table and a full refresh, expecting 1737.00 and 105 orders. |
| haiku-3 | yes | yes | 2 | Both; raised and dismissed the internal-processor exclusion, "that alone doesn't explain it", before settling on duplicates; fix removes the duplicates from the seed keeping one id per payment, then a full refresh of `daily_revenue` or a table. |
| opus-1 | yes | yes | 2 | Both: payment 121 as a copy of 11 and 127 on order 62 spotted in the sample, the six backfilled orders 100 to 105 missing; fix removes the re-inserted rows keeping the lowest id, the staging `qualify` dedupe as a stopgap, full refresh of `daily_revenue+`, and a `_loaded_at` window as a later change. |
| opus-2 | yes | yes | 2 | Both, with the month table attributing February's 100 to the six backfilled orders plus duplicates; fix deletes the duplicates from the seed keeping the lowest id, `daily_revenue` as a table and `dbt build --full-refresh`. |
| opus-3 | yes | yes | 2 | Both, with the split 180 over and 51 under worked to the cent (the 51 nets the 7 April replay inside the window against the 65.00 of late orders); fix removes the re-sent rows from the seed with the feed owner's confirmation, an optional staging dedupe, `dbt build --select stg_payments+ --full-refresh` and `daily_revenue` as a table. |

**Verification.** Every fix matches a shape in the Verification paragraph above.

**What it says.** Nine of nine on both defects and both fixes, the same as the nine curated runs. The standard sample's per-month `orders` against `daily_revenue` tables gave the split (74, 100, 57 and 0 summing to 231, with February six orders short) in place of the curated per-day mismatch sample, and the hash samples put a replay in view, payment 121 on January order 10 (and 127 on order 62), so the runs that used them named a concrete duplicate rather than inferring one from order 1; every run kept the six late orders and dropped only the replays. The slips change no verdict: sonnet-1 "The dashboard is stale on top of being inflated" when it sits below settlement, several runs calling the late-order gap 51 rather than 65 because the 7 April replay sits inside the window, and sonnet-2 and sonnet-3 lumping ids 114 and above together as the late load without separating the six real late payments 114 to 119 from the replays 120 to 131, which their group-by queries would do.
