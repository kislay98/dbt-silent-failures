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

## Opus runs (week 6)

Three runs of `claude-opus-5-5`, same command, prompt assembled by `check_case.py` from the project at f769fdf, before
the week 6 lookup extension, so the same project the other two models saw; verdicts in `results.csv`.

| Run | Root cause | Fix | Notes |
|---|---|---|---|
| opus-1 | yes | yes | `ordered_at` NULL on ids 66 to 73, `daily_revenue` right by luck; restore the timestamps from the admin at the source, make the loader fail loudly, full refresh. |
| opus-2 | yes | yes | Same; lists the day each lost order sits on from `daily_revenue`. |
| opus-3 | yes | yes | Same; warns not to full-refresh `daily_revenue` before the data is back. |

Opus: 3 of 3 found, 3 of 3 fixed.

## Standard sample

**Runs:** three per model, week 8, on the standard output sample (the same queries on every case: row counts, the raw
column list, monthly totals of the output tables and ten rows of every table picked by hash); `runs/standard/prompt.md`
is the frozen prompt every run answered and `runs/standard/` holds the responses. `claude -p --setting-sources ""
--model <model> --tools "" --output-format json` (Claude Code 2.1.299), project ad0b16b. Same rubric; Defects is how
many of the case's injected defects the run named as a cause.

| Run | Root cause | Fix | Defects | Notes |
|---|---|---|---|---|
| sonnet-1 | yes | yes | 1 | Eight `raw_orders` rows with NULL `ordered_at` (69 and 70 in the sample), the 8 and 149.00 NULL bucket matching the March gap, `daily_revenue` stale from before the NULLs; reload the timestamps from the shop admin, then `dbt build --full-refresh -s daily_revenue+ orders+ customers`, table materialisation as optional hardening; says the warehouse cannot recover the dates. |
| sonnet-2 | yes | yes | 1 | Same; reload upstream, "Don't coalesce to a made-up date in dbt", then a full refresh of `daily_revenue+ customers`; notes a full refresh would surface the NULL group and fail the existing `not_null`. |
| sonnet-3 | yes | yes | 1 | Same, calls the dashboard "stale, not right by design"; ask the ingestion owners and re-load, then `dbt build --full-refresh -s stg_orders+`, "Don't coalesce NULL dates into a fake date". |
| haiku-1 | yes | yes | 1 | Same; restore the timestamps from the source, "Don't backfill dates by guessing", but also adds `where order_date is not null` to `daily_revenue.sql` and a full refresh "so the stale March rows are dropped", which only gives the clean March if the reload runs first as listed; assumes the shop admin reads `daily_revenue`. |
| haiku-2 | yes | yes | 1 | Same, names every model the NULL passes through; confirm and reload `raw_orders` from the source, "Don't impute silently", then `dbt build --select daily_revenue --full-refresh` once the data is fixed; notes the nightly build does not run seeds. |
| haiku-3 | yes | yes | 1 | Same, framed as "two separate problems", the NULL batch and the stale window; restore the eight `ordered_at` upstream, then full refresh, warning that a refresh before the reload fails `not_null_daily_revenue_order_date` and "that failure is correct"; a NULL filter only if the dates cannot be recovered. |
| opus-1 | yes | yes | 1 | Eight March orders with NULL `ordered_at`, placed in March because ids 69 and 70 sit between 63 on 03-06 and 78 on 03-23; patch or reload the rows from the shop admin, fix the loader, then `dbt build --full-refresh -s stg_orders+`. |
| opus-2 | yes | yes | 1 | Same, 62 customers in `region_revenue` against 60 with a `first_order`; reload from the shop backend, "Don't guess dates in SQL", then full refresh of `stg_orders+`; notes the four seed tests never ran. |
| opus-3 | yes | yes | 1 | Same; restore from the source, `dbt build --select stg_orders+ --full-refresh` expected to reproduce 622.00 and 35, with a `where order_date is not null` in `daily_revenue.sql` as hardening only alongside the tests; its totals-only reconciliation test would not fail today, since `daily_revenue` still sums to 1672.00 and 99. |

**Verification.** haiku-1's `where order_date is not null` in `daily_revenue` is a no-op once the eight timestamps are reloaded at the source, which its fix lists first, so the end state is the clean data; every other fix matches a shape above.

**What it says.** All nine standard runs found the NULL `ordered_at` batch and gave the reload as the fix, matching the nine curated runs. The standard sample handed them the cause directly: the NULL month row (8 orders, 149.00) in the monthly `orders` query, which with 473 and 27 adds up to the 622 and 35 in the symptom text, and orders 69 and 70 with NULL `ordered_at` in the `raw_orders` and `stg_orders` samples; Opus also used the id ordering to place the rows in March. The curated Sonnet warning about full-refreshing before the reload survives in haiku-3 and in the step order of the others, but haiku-1 framed the refresh as dropping the stale March rows, and opus-3 claimed its totals-only test "fails today: 99 orders / 1672.00 against 91 / 1523.00" when the stale dashboard still carries the 149.00, so the totals agree and only a per-day test catches it. No fix went wrong.
