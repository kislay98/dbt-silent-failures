# Score: processor_history

**Runs:** three per model, `claude -p --setting-sources "" --model <model> --tools "" --output-format json` (Claude Code 2.1.296),
isolated from this machine's hooks, tools off, prompt assembled by `check_case.py` with the seeds hidden and a models-and-tests
build log (`Log: models only`, a real `dbt build --exclude resource_type:seed`), project f769fdf plus the week 6 lookup
extension (committed with week 6); each run header carries the sha256 of the prompt it answered. Sonnet is `claude-sonnet-5-5`,
Haiku is `claude-haiku-5-5`, Opus is `claude-opus-5-5`; responses in `runs/`.
Scored on the week 4 rubric: root cause found yes/no (the run names the change that broke the data), complete fix yes/no (strict,
every affected value right after the fix).

| Run | Root cause | Fix | Notes |
|---|---|---|---|
| sonnet-1 | yes | yes | `effective_from` makes the lookup a rate history, two `credit_card` rows, the join on method alone counts every card payment twice; validity windows with `lead()` and the rate in force on the order date, full refresh. |
| sonnet-2 | yes | yes | Same; `lead()` windows with an open-ended last row; names its assumptions (rate by order date, orders before the first rate, orphan payments). |
| sonnet-3 | yes | yes | Same; amounts from `payments` alone, fees by an as-of join per payment (`qualify row_number`), full refresh. |
| haiku-1 | yes | yes | Same; `lead()` windows on the order date, full refresh; points out that the seed tests never run in the nightly. |
| haiku-2 | yes | yes | Same; amounts from `payments` alone, latest rate per method, the rate-at-date rule left to finance. |
| haiku-3 | yes | yes | Same; one rate per payment by an as-of `qualify`, full refresh. |
| opus-1 | yes | yes | `effective_from` makes the lookup a rate history, two `credit_card` rows, fees at the sum of both rates (0.058); `lead()` windows and the rate in force on the order date, full refresh; notes the seed tests never run in the nightly. |
| opus-2 | yes | yes | Same; handles exact duplicates and orders before the first rate explicitly. |
| opus-3 | yes | yes | Same; amounts from `payments` alone and one rate per payment by an as-of `qualify`, full refresh. |

**Verification.** The latest row per method, an effective-dated join on the order date, and removing the Adyen row all
restore `orders` and the day-by-day aggregate, because the rate did not change. A `unique` test on
(`payment_method`, `effective_from`) returns 0 rows on the broken data, so it is no guard for this; `unique` on
`payment_method` alone fails with 1 row and would also fire on legitimate history. The `orders`-against-`stg_payments`
reconciliation fails with 1 row on the broken data and passes clean.

**What it says.** Nine of nine on both counts. The `effective_from` column in the raw column list told every run what the lookup had become; eight picked the rate in force on the order date and one the latest row, and both give the clean output because the rate did not change.

## Standard sample

**Runs:** three per model, week 8, on the standard output sample (the same queries on every case: row counts, the raw
column list, monthly totals of the output tables and ten rows of every table picked by hash); `runs/standard/prompt.md`
is the frozen prompt every run answered and `runs/standard/` holds the responses. `claude -p --setting-sources ""
--model <model> --tools "" --output-format json` (Claude Code 2.1.299), project ad0b16b. Same rubric; Defects is how
many of the case's injected defects the run named as a cause.

| Run | Root cause | Fix | Defects | Notes |
|---|---|---|---|---|
| sonnet-1 | yes | yes | 1 | Two `credit_card` rows in `raw_payment_methods`, `stripe` from 2018-01-01 and `adyen` from 2018-03-01, the join in `order_payments` ignores `effective_from` and counts every card payment twice; `lead()` validity windows and the rate in force on the order date, `dbt build --full-refresh`; per-order and total reconciliations against `stg_payments`, with a `(payment_method, effective_from)` combination test "as a minimum" that would not fire here. |
| sonnet-2 | yes | yes | 1 | Same; `lead()` windows on the order date, `dbt build --full-refresh -s orders daily_revenue+`; a per-method reconciliation of `orders` against `stg_payments` and the same combination test. |
| sonnet-3 | yes | yes | 1 | Same, reads April's 163 in `daily_revenue` as the doubled window against a correct 139; `lead()` windows with an inner join to `orders`, `dbt build --full-refresh -s orders+`; drafts an overlap test it then drops for a per-order reconciliation. |
| haiku-1 | yes | yes | 1 | Same, with order 25's fee 1.38 worked back to two card rows; a `priced_payments` CTE with a DuckDB `asof left join` on the order date, one row per payment, `dbt build --select orders+ --full-refresh`; per-order reconciliation; says a `unique` test on the method alone would reject valid history and that the seed tests never ran (28 of 32). |
| haiku-2 | yes | yes | 1 | Same; one lookup row per payment by `qualify row_number()` over `payment_id` on `effective_from` desc after a join on the order date, `dbt build --select orders+ --full-refresh`, with deleting the `stripe` row offered as a data-only alternative; per-method reconciliation. |
| haiku-3 | yes | yes | 1 | Same; amounts summed from `payments` alone in `order_amounts` and fees by an `asof left join` on the order date in `order_fees`, `dbt run --select orders daily_revenue --full-refresh`; per-order reconciliation and a `(payment_method, effective_from)` singular test; reads the 28 of 32 tests as the seed tests not running. |
| opus-1 | yes | yes | 1 | Same; order date joined onto `payments`, `lead()` windows and the effective-dated join, `dbt build --select orders+ --full-refresh`; per-order reconciliation and a singular grain test on `(payment_method, effective_from)`, noting a plain `unique` on the method "would now be wrong". |
| opus-2 | yes | yes | 1 | Same; `lead()` windows and the rate in force on the order date, `dbt build -s orders+ --full-refresh`; reconciliation and the combination test; wants the raw tables declared as sources so their tests run nightly. |
| opus-3 | yes | yes | 1 | Same; `lead()` windows with an inner join to `orders`, `dbt build -s orders+ --full-refresh`; per-order reconciliation plus a `daily_revenue`-against-`orders` check, and says the combination test "would not have caught this". |

**Verification.** Every fix matches a shape in the Verification paragraph above.

**What it says.** Nine of nine on both counts, the same as the curated runs. The raw column list showed `effective_from` and the lookup sample listed both `credit_card` rows with the `adyen` one dated 2018-03-01, so every run read the table as a rate history off the sample and took the doubling in January and February as proof the join ignores dates; all nine chose the rate in force on the order date (the curated set had one latest-row pick), and several said outright not to delete the Adyen row. All nine also read the `daily_revenue` monthly table (1696, April 163 against 139) as the stale incremental and called a full refresh, and four read the log's 28 of 32 tests as the seed tests never running. Nothing went wrong; the only soft spot is that four runs still offer a `(payment_method, effective_from)` uniqueness test that passes on the broken data, where haiku-1, opus-1 and opus-3 say so.
