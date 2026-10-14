# Score: region_fanout_quiet

**Runs:** three per model, `claude -p --setting-sources "" --model <model> --tools "" --output-format json` (Claude Code 2.1.296),
isolated from this machine's hooks, tools off, prompt assembled by `check_case.py` with the seeds hidden, project
7d5b525 plus the week 3 working tree. Sonnet is `claude-sonnet-5-5`, Haiku is `claude-haiku-5-5`; responses in `runs/`.

| Run | Root cause | Fix | Notes |
|---|---|---|---|
| sonnet-1 | yes | yes | 102 rows for 100 customers off the build log, 164.00, same-region duplicates deduced from the order total of 99. Staging dedupe. |
| sonnet-2 | yes | yes | Same; staging dedupe with a tie-breaker; asks whether revenue should follow the region at order time. |
| sonnet-3 | yes | yes | Same; also warns that the inner join drops customers with no region. |
| haiku-1 | yes | yes | Same; staging dedupe; proposes an "UNASSIGNED" region for unmapped customers. |
| haiku-2 | yes | yes | Same; staging dedupe and `region_revenue` rewritten over `orders`. |
| haiku-3 | yes | yes | Same; dedupe inside the model and the rewrite over `orders`. |

**Verification.** Both fix shapes restore every region to the clean values. The reconciliation test fails with 1 row
on the broken data and passes clean. The confirmation query returns customers 3 and 51 with one region each and
164.00 of payments.

**What it says.** Six of six, no harder than `region_fanout`: the seed row count still sits in the build log
(`INSERT 102`), and every run read it. A real harder variant needs the harness to drop seed lines from the log, or
a single re-sent row.
