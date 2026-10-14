# Score: payment_method_renamed

**Runs:** three per model, `claude -p --setting-sources "" --model <model> --tools "" --output-format json` (Claude Code 2.1.296),
isolated from this machine's hooks, tools off, prompt assembled by `check_case.py` with the seeds hidden and the seed lines dropped
from the build log (`Log: models only`), project bef6b69. Sonnet is `claude-sonnet-5-5`, Haiku is `claude-haiku-5-5`; responses in `runs/`.
Scored on the week 4 rubric: root cause found yes/no (the run names the change that broke the data), complete fix yes/no (strict,
every affected value right after the fix).
The runs answered the week 4 form of the models-only log (seed lines filtered out of the full build log, so node numbers started at 5 of 40); since T1-shlok-30 the harness builds that log with `--exclude resource_type:seed`, so `--prompt` now shows 34 nodes numbered from 1. Nothing else in the prompt changed.

| Run | Root cause | Fix | Notes |
|---|---|---|---|
| sonnet-1 | yes | yes | New `method` column, `payment_method` NULL from March; `coalesce(payment_method, method)` in staging; `not_null` plus a methods-add-up-to-amount test. |
| sonnet-2 | yes | yes | Same; adds a source contract or column-set check against future drift. |
| sonnet-3 | yes | yes | Same; names orders 57 to 63 in the sample as the first affected rows. |
| haiku-1 | yes | yes | Same; explains why `accepted_values` passes (`not in` is NULL for NULL) and why a warehouse-side `update` would be overwritten by the next load. |
| haiku-2 | yes | yes | Same; warns that a plain `coalesce` is wrong if `method` uses other labels. |
| haiku-3 | yes | yes | Same; dates the break to 1 March from the sample and calls `coalesce` a stopgap until the feed is fixed. |

**Verification.** Both fix shapes, `coalesce(payment_method, method)` and `method as payment_method`, restore every row of
`orders`. The `not_null` test on `stg_payments.payment_method` fails with 49 rows on the broken data and passes clean;
the methods-add-up test fails with 42 rows; dbt's `accepted_values` returns 0 rows on both, because it skips NULL,
which every run said in its own words.

**What it says.** Six of six. With the column list in the prompt the rename is a short inference, and every run also
asked for the vocabulary of `method` to be checked before trusting the `coalesce`.

## Opus runs (week 6)

Three runs of `claude-opus-5-5`, same command, prompt assembled by `check_case.py` from the project at f769fdf, before
the week 6 lookup extension, so the same project the other two models saw; verdicts in `results.csv`.

| Run | Root cause | Fix | Notes |
|---|---|---|---|
| opus-1 | yes | yes | The feed writes `method` from March and leaves `payment_method` NULL, the case buckets fall to 0 while `amount` holds; `coalesce(payment_method, method)` in staging with a mapping if the labels differ; `not_null` plus a buckets-add-up test. |
| opus-2 | yes | yes | Same. |
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
| sonnet-1 | yes | yes | 1 | New `method` column in `raw_payments`, `payment_method` NULL on the later rows with the cutover around 1 March, the pivot and the fee join both miss on NULL while `amount` holds; `coalesce(payment_method, method)` in `stg_payments.sql` then `dbt build`; `not_null` plus a methods-add-up-to-amount test. |
| sonnet-2 | yes | yes | 1 | Same; `coalesce(payment_method, method)` then `dbt build`, and once `payment_method` is dropped "the model will fail on a missing column, and that is the right failure". |
| sonnet-3 | yes | yes | 1 | Same; `coalesce(payment_method, method)` then `dbt build --full-refresh -s stg_payments+`, with a check that March card volume lands at 334.00 and fees come back. |
| haiku-1 | yes | yes | 1 | Same, with the Jan and Feb buckets shown to add up to `amount` and March and April not; a warehouse `update raw_payments set payment_method = method where payment_method is null` plus `coalesce(payment_method, method)` as the model guard, then `dbt build --select stg_payments+`. |
| haiku-2 | yes | yes | 1 | Same; `coalesce(payment_method, method)` then `dbt build --select stg_payments+`, after checking that `method` uses the same values and casing as `payment_method`. |
| haiku-3 | yes | yes | 1 | Same; `coalesce(payment_method, method)` in staging rather than a raw `update` the nightly load would overwrite, then `dbt build --select stg_payments+`. |
| opus-1 | yes | yes | 1 | Same, cutover placed at order 57 on 2018-03-01; `coalesce(method, payment_method)` then `dbt build`, with a query that the two columns never disagree where both are set. |
| opus-2 | yes | yes | 1 | Same; `coalesce(method, payment_method)` then `dbt build -s stg_payments+ --full-refresh`, with checks that no row has both columns NULL. |
| opus-3 | yes | yes | 1 | Same; `coalesce(method, payment_method)` then `dbt build`, and March fees expected back at 334 times 0.029 plus the bank transfer fees. |

**Verification.** Every fix matches a shape in the Verification paragraph above.

**What it says.** Nine of nine on both, the same as the six of six and the Opus three of three on the curated prompt. The standard sample gave the runs the `method` column in the raw column list, seven `raw_payments` rows with `payment_method` NULL and `method` filled against three rows with both, three NULL rows in `stg_payments`, and the monthly `orders` table where the January and February buckets add up to `amount` while March and April show 0.00 for every method and for `fees`; every run used all of these, dated the cutover to 1 March, and also caught `fees` falling to 0.00 through the same NULL join key, which the symptom text never mentioned. The only shape differences from the curated runs are the Opus order inside the `coalesce` and haiku-1's warehouse `update` alongside the model change, and neither changes a value.
