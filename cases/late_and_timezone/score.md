# Score: late_and_timezone

**Runs:** three per model, `claude -p --setting-sources "" --model <model> --tools "" --output-format json` (Claude Code 2.1.296),
isolated from this machine's hooks, tools off, prompt assembled by `check_case.py` with the seeds hidden and a models-and-tests
build log (`Log: models only`, a real `dbt build --exclude resource_type:seed`), project 8ee5734 (week 6 committed, with the
lookup extension); each run header carries the sha256 of the prompt it answered. Sonnet is `claude-sonnet-5-5`, Haiku is
`claude-haiku-5-5`, Opus is `claude-opus-5-5`; responses in `runs/`.
Scored on the week 4 rubric: root cause found yes/no (the run names the change that broke the data), complete fix yes/no (strict,
every affected value right after the fix).

| Run | Root cause | Fix | Notes |
|---|---|---|---|
| sonnet-1 | yes | yes | Both: the window keyed on order date, and the backfill "on a different time basis from the rest of the table", 5h30 behind, read from ops' statement that every earlier day matched; ids 100 to 105 shifted in staging as a stopgap with the ingestion job to fix, `daily_revenue` as a table, full refresh; the docs' "Date (UTC)" flagged as wrong. |
| sonnet-2 | yes | yes | Both: "the backfill's `ordered_at` is in UTC, but the rest of the table is in shop local time"; `update raw_orders ... + interval 5.5 hour where id between 100 and 105`, table, full refresh, docs corrected. |
| sonnet-3 | yes | yes | Both, all six rows checked against the sync screen; the same update, table or a `loaded_at` watermark, full refresh; the customers whose dates move listed. |
| haiku-1 | no | no | "The dashboard is stale, not wrong": the window found, the offset computed for all six rows, then "the models are correct as documented" and February left at 32 and 474.00; notes that a UTC feed would not fit "agreed every day since January" and keeps UTC anyway. |
| haiku-2 | no | no | Both named, the second as "the model's definition is wrong, not the data": every row converted from UTC to local with a `shop_timezone` var, which moves the 99 shop orders; table. |
| haiku-3 | yes | yes | Both: "the earlier rows are probably already in local time and only the backfill is UTC", from the reconciliation; the ingestion job to reload the backfill in the feed's convention, table; the staging stopgap left without its row filter, "don't apply this to all rows". |
| opus-1 | yes | yes | Both; the update on ids 100 to 105 with the ingestion job to fix, table, full refresh; the three customers whose dates move; asks which convention is canonical, "the 6 backfill rows are the odd ones out either way". |
| opus-2 | yes | yes | Both; same shape; "`stg_orders.sql` is fine, `cast(ordered_at as date)` is correct as long as the raw data is consistent". |
| opus-3 | yes | yes | Both; same shape, with the docs' "Date (UTC)" corrected to local; "`raw_orders` has no store or batch column, so this belongs in the data layer, not in staging". |

**Verification.** `case when order_id >= 100 then ordered_at + interval '5 hours 30 minutes' else ordered_at end` in `stg_orders` (or the six rows restated at the source) with `daily_revenue` rebuilt from `orders` restores every table: 105 orders and 1737.00, February 33 orders and 480.00 in both models, January 29 and 496.00. The rebuild alone leaves February at 32 and 474.00 with the 1 February order in January; the shift alone leaves `daily_revenue` at 415.00; shifting every row, or every row from 1 March, moves the shop's own orders a day. The per-day reconciliation fails with 6 rows on the broken data and passes clean; a check that no backfilled order falls outside February fails with 1 row.

**Prompt note.** The first prompt said ops reconciles `orders` by `order_date` against the admin's local time but not that the two had agreed before this load; as on `rename_and_utc`, with the docs calling `order_date` "Date (UTC)", every Sonnet and Haiku run read the whole feed as UTC and the shifted backfill as "timezone, not a bug", while all three Opus runs inferred the local convention from the reconciliation. The same clause as on `rename_and_utc` was added ("and until this load the two agreed on every day since January"), the nine runs were set aside and the case run again; the table above is the second set and only it is in `results.csv`. The nine first runs are kept in `runs/first_prompt/` with the prompt they answered (sha256 `0a1f2799e0c1`), scored on the same rubric:

| Run | Root cause | Fix | Notes |
|---|---|---|---|
| sonnet-1 | no | no | The incremental window, with the six backfilled orders traced day by day; the 5h30 offset found from order 100 and called "timezone, not a bug": `order_date` is documented UTC, so the admin's February will differ. `daily_revenue` as a table plus an `order_date_local` column shifted for every row. |
| sonnet-2 | no | no | Same: "a timezone boundary, not a bug"; table, full refresh, a local-date column for ops. |
| sonnet-3 | no | no | Calls the offset "a second problem" but reads the whole feed as UTC per the docs; adds a local date converted for every row and suggests the dashboard group by it, which moves the 99 shop-local orders. |
| haiku-1 | no | no | The window; the offset "a separate, expected discrepancy" that "does not explain the mismatch"; table; a local column left to ops. |
| haiku-2 | no | no | Same, "a separate, secondary issue"; table; also a red herring on the payment-method lookup fanning out. |
| haiku-3 | no | no | Same, "a reconciliation difference, not the cause", with the zone guessed as UTC+10; table, full refresh. |
| opus-1 | yes | yes | Both: the window, and "the backfill was exported in a different timezone convention from the shop's local time", all six rows checked against the sync screen; `update raw_orders ... + 5h30 where id between 100 and 105` (or the feed re-delivered), `daily_revenue` as a table, full refresh; asks ops to confirm one non-backfilled order first, then "the docs are wrong too". |
| opus-2 | yes | yes | Both: "the backfill path converted these six rows to UTC; the regular sync doesn't", because `order_date` has always reconciled against the admin; the same update, table, rebuild; corrects the docs. |
| opus-3 | yes | yes | Both, each named as enough on its own for a mismatch; a guarded update that cannot shift the rows twice, table, full refresh; the per-day test "would not catch A", which needs a `timestamptz` contract at ingestion. |

**What it says.** Seven of nine on the amended prompt against three of nine on the first: one clause moved Sonnet from 0 to 3 and Haiku from 0 to 1, and Opus had found it without the clause. Of the three defects in this benchmark that need an inference about what a timestamp means, this is the only one where the two smaller models reach a full fix, and only once told in words that the old rows reconciled. The two Haiku misses are the two readings seen before: the feed is UTC and the admin is wrong (haiku-1), or the model is wrong and every row needs converting (haiku-2).
