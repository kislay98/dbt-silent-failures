# Score: transfers_in_micros

**Runs:** three per model, `claude -p --setting-sources "" --model <model> --tools "" --output-format json` (Claude Code 2.1.296),
isolated from this machine's hooks, tools off, prompt assembled by `check_case.py` with the seeds hidden and a models-and-tests
build log (`Log: models only`, a real `dbt build --exclude resource_type:seed`), project bef6b69; each run header carries the
sha256 of the prompt it answered. Sonnet is `claude-sonnet-5-5`, Haiku is `claude-haiku-5-5`; responses in `runs/`.
Scored on the week 4 rubric: root cause found yes/no (the run names the change that broke the data), complete fix yes/no (strict,
every affected value right after the fix).

| Run | Root cause | Fix | Notes |
|---|---|---|---|
| sonnet-1 | yes | yes | Bank transfers from payment 83 (order 73, 16 March) 10,000 times too large; calls the unit "1/10,000-dollar" (it is millionths), the factor and boundary are right; `update ... amount / 10000` from the first bad id, full refresh. |
| sonnet-2 | yes | yes | Millionths of a dollar from payment 83; staging rule `amount / 1000000.0` for those rows; full refresh. |
| sonnet-3 | yes | yes | Micro-units from payment 83; `update` guarded by `amount >= 10000`; plausibility test. |
| haiku-1 | yes | yes | 10,000x on bank transfers from about payment 83; reconciles March and April to the statement; correct the raw rows at the source after confirming scope. |
| haiku-2 | no | yes | Reads the inflation as "not a constant factor" (190,000 and 260,000 "imply different factors"; they are 19.00 and 26.00 times 10,000) and leaves 114.00 "unexplained"; its fix, re-ingesting every raw row over 100,000 from the processor, still covers all ten rows. |
| haiku-3 | yes | yes | 10,000x from 16 March, micros implied by 3,000,000 for 3.00; ingestion fix plus an interim `update` for confirmed ids. |

**Verification.** Dividing the bank transfers from 15 March on (by order date, by payment id 83 and later, by a
1000.00 threshold, or by raw amount over 100,000) by 10,000 restores `orders` and `region_revenue`; dividing every
bank transfer shrinks the 23 earlier ones. A payment ceiling test fails with 10 rows on the broken data and passes
clean; so does the settlement total.

**What it says.** Five of six found, six of six fixed. The one miss is arithmetic: Haiku-2 did not divide 190,000 by
19 and concluded the factors differed, then recommended a reload that happens to cover every affected row.
