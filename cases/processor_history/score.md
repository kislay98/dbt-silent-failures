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
