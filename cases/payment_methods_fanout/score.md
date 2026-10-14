# Score: payment_methods_fanout

**Runs:** three per model, `claude -p --setting-sources "" --model <model> --tools "" --output-format json` (Claude Code 2.1.296),
isolated from this machine's hooks, tools off, prompt assembled by `check_case.py` with the seeds hidden and a models-and-tests
build log (`Log: models only`, a real `dbt build --exclude resource_type:seed`), project f769fdf plus the week 6 lookup
extension (committed with week 6); each run header carries the sha256 of the prompt it answered. Sonnet is `claude-sonnet-5-5`,
Haiku is `claude-haiku-5-5`, Opus is `claude-opus-5-5`; responses in `runs/`.
Scored on the week 4 rubric: root cause found yes/no (the run names the change that broke the data), complete fix yes/no (strict,
every affected value right after the fix).

| Run | Root cause | Fix | Notes |
|---|---|---|---|
| sonnet-1 | yes | yes | Two rows for `bank_transfer` in the lookup, the `order_payments` join fans out before the sums; one row per method at the source, full refresh; fees to be rechecked against the processor. |
| sonnet-2 | yes | yes | Same; one row per method at the source, and the fee sum moved into its own CTE so a bad lookup can only distort `fees`; full refresh. |
| sonnet-3 | yes | yes | Same; data fix and full refresh, a separate fee step offered as hardening. |
| haiku-1 | yes | yes | Same; remove the stale row, a staged `stg_payment_methods` with a `unique` test inside the build path, full refresh; a dbt unit test that pins the fan-out. |
| haiku-2 | yes | yes | Same; data fix plus the fee CTE split, full refresh; read the summed fee rate (0.01) off the sample. |
| haiku-3 | yes | yes | Same; data fix and full refresh; declines a `max()`/`distinct` workaround as a hider. |
| opus-1 | yes | yes | Two rows for `bank_transfer` in the lookup, the join in `order_payments` fans out before the sums, fees charged at the sum of both rates; one row per method at the source, amounts from `payments` alone with the fee sum in its own CTE, full refresh; wants the raw tables declared as sources so their tests run nightly. |
| opus-2 | yes | yes | Same; data fix plus amounts computed without the join, a `max(fee_rate)` lookup per payment as the fee shape, full refresh. |
| opus-3 | yes | yes | Same; data fix plus the fee CTE split, full refresh; a table of the evidence down to the two bank-transfer rates summing to 1%. |

**Verification.** Collapsing the lookup to one row per method before the join, by `group by` with `max(fee_rate)` or by
a `row_number` pick, restores `orders` and the day-by-day aggregate exactly; removing the extra row at the source is the
clean seed. Halving the bank transfer column in `orders` leaves `fees` doubled (0.26 against 0.13 on order 10). A `unique`
test on the lookup key fails with 1 row on the broken data, the `orders`-against-`stg_payments` reconciliation with 1
row; both pass clean.

**What it says.** Nine of nine on both counts. A fan-out through the new join is as findable as the regions one: every run saw `stg_payments` right and `orders` wrong, and the only thing between them is the lookup join. Six of nine moved the fee sum out of the amount aggregate so the lookup can never touch revenue again.

## Standard sample

**Runs:** three per model, week 8, on the standard output sample (the same queries on every case: row counts, the raw
column list, monthly totals of the output tables and ten rows of every table picked by hash); `runs/standard/prompt.md`
is the frozen prompt every run answered and `runs/standard/` holds the responses. `claude -p --setting-sources ""
--model <model> --tools "" --output-format json` (Claude Code 2.1.299), project ad0b16b. Same rubric; Defects is how
many of the case's injected defects the run named as a cause.

| Run | Root cause | Fix | Defects | Notes |
|---|---|---|---|---|
| sonnet-1 | yes | yes | 1 | Two `bank_transfer` rows in `raw_payment_methods`, the `order_payments` join in `orders.sql` fans out before the sums; collapses the lookup with `group by payment_method` and `max(fee_rate)`, deleting one seed row left to the feed owner, `dbt build --full-refresh -s orders+ raw_payment_methods`; `unique` on the key and an `orders`-against-`stg_payments` reconciliation. |
| sonnet-2 | yes | yes | 1 | Same, and reads `daily_revenue` at 1696 as January to March built before the row arrived and April rebuilt doubled; `group by` with `max(fee_rate)` in `orders.sql` plus one row per method at the source, `dbt build --full-refresh -s orders daily_revenue`; the same two tests. |
| sonnet-3 | yes | yes | 1 | Same; removes the `westpac` row from the seed and shows the four-row CSV, `dbt seed` then `dbt build --full-refresh`, the `group by` guard offered as optional; `unique` plus reconciliation. |
| haiku-1 | yes | yes | 1 | Same; one row per method at the source with the processor to keep left to finance ("the fee rate is the same for both, so the fees are unaffected either way"), `dbt seed` then `dbt build --full-refresh`, no SQL change; `unique` on the key, reconciliation, and reads the log's 28 of 32 tests as the seed tests never running. |
| haiku-2 | yes | yes | 1 | Same, reads order 20's fee 0.15 as 2 x 15 x 0.005; `group by` with `max(fee_rate)` in `orders.sql` plus a dedupe at the source or loader, `dbt build --select orders+ --full-refresh`; per-order reconciliation against `stg_payments` and `unique` on the seed key. |
| haiku-3 | yes | yes | 1 | Same; one row per method at the source plus the `group by` guard in `orders.sql`, `dbt build --select raw_payment_methods+ --full-refresh`; `unique` and per-order reconciliation; says the stale window is the one part of `daily_revenue` it cannot confirm. |
| opus-1 | yes | yes | 1 | Same; one row per method at the source (processor to keep for finance) plus `group by` with `max(fee_rate)` in `orders.sql`, `dbt build --select orders+ --full-refresh`; `unique` on the key and per-order reconciliation. |
| opus-2 | yes | yes | 1 | Same, and reads `daily_revenue` April 163 as three days rebuilt doubled; a new `models/staging/stg_payment_methods.sql` with `select distinct payment_method, fee_rate` (drops `processor`, so one row per method because both rates are 0.005) joined from `orders.sql`, full refresh; its rebuild selector `-s orders+` leaves the new model out, which is not scored; `unique` on the staged key and a reconciliation. |
| opus-3 | yes | yes | 1 | Same; `group by` with `max(fee_rate)` in `orders.sql` only, the seed grain re-documented as method and processor rather than fixed, `dbt build --select orders+ --full-refresh`; reconciliation plus `unique_combination_of_columns` on method and processor (which passes on the broken data) and a one-rate-per-method singular test. |

**Verification.** opus-2's new `stg_payment_methods` with `select distinct payment_method, fee_rate` leaves one `bank_transfer` row because both rows carry 0.005, so on this lookup it is the one-row-per-method dedupe above and restores `orders`; its rebuild selector `-s orders+` skips the new model, which is not scored (rebuild commands were not scored on the curated runs either, several of which have the same slip). Every other fix matches a shape above.

**What it says.** Nine of nine named the cause and eight of nine fixed it, against nine and nine on the curated prompt. The standard sample handed over the duplicate: the row-count table shows `raw_payment_methods` at 5 and its sample lists both `bank_transfer` rows, so every run read the cause off the sample and checked it against 2083 minus 1672 = 411 and the doubled monthly column, and all nine read the `daily_revenue` monthly table (1696, April 163 against 178) as the stale incremental and called a full refresh. None moved the fee sum into its own CTE, where six of nine curated runs did; every run went to a `group by` with `max(fee_rate)` or a source dedupe instead. The one miss is opus-2, whose new `stg_payment_methods` model sits outside its "`dbt build --full-refresh -s orders+`".
