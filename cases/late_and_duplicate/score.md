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
