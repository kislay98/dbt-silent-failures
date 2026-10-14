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
