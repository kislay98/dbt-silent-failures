# Score: join_drops_rows

**Runs:** three per model, `claude -p --setting-sources "" --model <model> --tools "" --output-format json` (Claude Code 2.1.296),
isolated from this machine's hooks, tools off, prompt assembled by `check_case.py` with the seeds hidden, project
7d5b525 plus the week 3 working tree. Sonnet is `claude-sonnet-5-5`, Haiku is `claude-haiku-5-5`; responses in `runs/`.

| Run | Root cause | Fix | Notes |
|---|---|---|---|
| sonnet-1 | yes | yes | Orphan payments dropped by the left join in `orders` and the inner join in `region_revenue`, 140.00, order 25's third payment. Said the key "may be wrong ids, or null" and that it cannot invent the ids. Fix: correct the references at the source, no model patch, full-refresh `daily_revenue`. |
| sonnet-2 | yes | yes | Same chain; adds that if orphans can be legitimate they should land in an "unknown" bucket rather than vanish. |
| sonnet-3 | yes | yes | Same chain; also names the leak in `customers`. |
| haiku-1 | yes | yes | Same chain; ruled out the cents conversion and a regions fan-out explicitly before settling. |
| haiku-2 | partly | no | "The payments are fine. The orders are incomplete": attributes the orphans to orders missing from `raw_orders` and proposes loading them; calls order 25 "not explained". Its diagnostic uses `NOT IN (select id ...)`, which returns no rows when the key is NULL (0 rows against 13 with a left join, checked). |
| haiku-3 | yes | yes | Orphans dropped by the joins; guessed order 25's payment was keyed to a neighbouring order (wrong, hedged with a query); fix: resolve at the source either way. |

**Verification.** `not_null` on `stg_payments.order_id` fails with 13 rows on the broken data. dbt's `relationships` test
passes on it, because the test skips NULL keys, so a run that relied on `relationships` alone would have stayed blind;
every run paired it with `not_null`. The payments-against-orders reconciliation fails with 1 row on the broken data.
All of them pass on clean data (43/43). No run proposed a warehouse-side repair, which is right: the references are gone.

**What it says.** Five of six. The miss is a direction error, not a mechanism error: the model saw unmatched payments
and decided the orders were missing rather than the keys, and its own check would have hidden the evidence.
