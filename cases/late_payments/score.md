# Score: late_payments

**Runs:** three per model, `claude -p --setting-sources "" --model <model> --tools "" --output-format json` (Claude Code 2.1.296),
isolated from this machine's hooks, tools off, prompt assembled by `check_case.py` with the seeds hidden and the seed lines dropped
from the build log (`Log: models only`), project bef6b69. Sonnet is `claude-sonnet-5-5`, Haiku is `claude-haiku-5-5`; responses in `runs/`.
Scored on the week 4 rubric: root cause found yes/no (the run names the change that broke the data), complete fix yes/no (strict,
every affected value right after the fix).
The runs answered the week 4 form of the models-only log (seed lines filtered out of the full build log, so node numbers started at 5 of 40); since T1-shlok-30 the harness builds that log with `--exclude resource_type:seed`, so `--prompt` now shows 34 nodes numbered from 1. Nothing else in the prompt changed.

| Run | Root cause | Fix | Notes |
|---|---|---|---|
| sonnet-1 | yes | yes | Late payments (ids 114 to 116 against 35 to 41 for the same orders) and the three-day window; full refresh now, table materialisation, a `loaded_at` on payments if it must stay incremental; reconciliation test by day. |
| sonnet-2 | yes | yes | Same, with the per-order table of original and late legs; notes the sample only shows three of the eight days' payments. |
| sonnet-3 | yes | yes | Same; drops the incremental config entirely. |
| haiku-1 | yes | yes | Same; maps payments 114, 115, 116 to the 02-02, 02-04 and 02-10 gaps. |
| haiku-2 | yes | yes | Same; also updates the stale model description. |
| haiku-3 | yes | yes | Same; insists the reconciliation test must cover all history, not a window. |

**Verification.** `dbt build --full-refresh --select daily_revenue` on the broken data makes `daily_revenue` equal the
day-by-day aggregate of `orders` (1742.00, February 485.00), and a table materialisation does the same on every run.
The reconciliation test fails with 8 rows on the broken data, passes clean and passes after the fix. A 90-day lookback,
which no run proposed, would also have reached back to 9 January.

**What it says.** Six of six. The late payment ids in the sample and the model's own comment ("a day or two") gave
every run the mechanism, and all six chose to drop the incremental logic rather than widen the window.

## Opus runs (week 6)

Three runs of `claude-opus-5-5`, same command, prompt assembled by `check_case.py` from the project at f769fdf, before
the week 6 lookup extension, so the same project the other two models saw; verdicts in `results.csv`.

| Run | Root cause | Fix | Notes |
|---|---|---|---|
| opus-1 | yes | yes | Late second payments (114 and up) on February orders, the window keyed on order date; full refresh, then a table or a changed-day rebuild. |
| opus-2 | yes | yes | Same. |
| opus-3 | yes | yes | Same, with an incremental variant that writes only changed days. |

Opus: 3 of 3 found, 3 of 3 fixed.
