# Score: null_order_dates

**Runs:** three per model, `claude -p --setting-sources "" --model <model> --tools "" --output-format json` (Claude Code 2.1.296),
isolated from this machine's hooks, tools off, prompt assembled by `check_case.py` with the seeds hidden and a models-and-tests
build log (`Log: models only`, a real `dbt build --exclude resource_type:seed`), project bef6b69; each run header carries the
sha256 of the prompt it answered. Sonnet is `claude-sonnet-5-5`, Haiku is `claude-haiku-5-5`; responses in `runs/`.
Scored on the week 4 rubric: root cause found yes/no (the run names the change that broke the data), complete fix yes/no (strict,
every affected value right after the fix).

| Run | Root cause | Fix | Notes |
|---|---|---|---|
| sonnet-1 | yes | yes | Orders 66 to 73 lost `ordered_at` on ingestion; restore at the source, rebuild `orders` and `customers`, and do not full-refresh `daily_revenue` first because it holds the only correct March. |
| sonnet-2 | yes | yes | Same, same warning about the full refresh; no `coalesce` of dates in dbt. |
| sonnet-3 | yes | yes | Same; backs up `daily_revenue` first; offers the id-to-day mapping as a stopgap to confirm against the admin (it is right: 66 on the 10th, 67 to 69 on the 11th, 70 and 71 on the 12th, 72 on the 14th, 73 on the 16th). |
| haiku-1 | yes | yes | Same; reload the eight timestamps from the source, then `dbt build --full-refresh -s daily_revenue+`; explains why `not_null` on `daily_revenue.order_date` gives false comfort. |
| haiku-2 | yes | yes | Same; names every model the NULL passes through. |
| haiku-3 | yes | yes | Same; "don't infer the timestamps from neighbours". |

**Verification.** The timestamps are not in the warehouse, so the only complete fix is the reload, which is the clean
seed. Dropping the NULL-date rows in staging hides the loss and leaves `orders` eight rows short. `not_null` on
`stg_orders.order_date` fails with 8 rows on the broken data and passes clean; so does a reconciliation of March
revenue, or of `orders` by day, against `daily_revenue`.

**What it says.** Six of six. The NULL bucket in the monthly query and the eight NULL timestamps in the staging sample
left little to infer; the interesting part is that all three Sonnet runs warned that a full refresh of the dashboard
before the reload would destroy the only correct copy of March, which the ground truth had not spelled out.
