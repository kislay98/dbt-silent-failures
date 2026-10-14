# Score: status_new_value

**Runs:** three per model, `claude -p --setting-sources "" --model <model> --tools "" --output-format json` (Claude Code 2.1.296),
isolated from this machine's hooks, tools off, prompt assembled by `check_case.py` with the seeds hidden and the seed lines dropped
from the build log (`Log: models only`), project bef6b69. Sonnet is `claude-sonnet-5-5`, Haiku is `claude-haiku-5-5`; responses in `runs/`.
Scored on the week 4 rubric: root cause found yes/no (the run names the change that broke the data), complete fix yes/no (strict,
every affected value right after the fix).
The runs answered the week 4 form of the models-only log (seed lines filtered out of the full build log, so node numbers started at 5 of 40); since T1-shlok-30 the harness builds that log with `--exclude resource_type:seed`, so `--prompt` now shows 34 nodes numbered from 1. Nothing else in the prompt changed.

| Run | Root cause | Fix | Notes |
|---|---|---|---|
| sonnet-1 | yes | yes | New terminal status from March, `accepted_values` widened to keep the build green; `is_completed` flag in `stg_orders` used by `customers`; proposes a status map seed with a `relationships` test so a new status fails until classified. |
| sonnet-2 | yes | yes | Same flag; also offers the staging mapping if `delivered` is a rename; a test that every status is classified. |
| sonnet-3 | yes | yes | Same flag; finance control totals as a seed. |
| haiku-1 | yes | yes | `delivered` counted by the admin, not by the models; the list widened "without a revenue decision"; `status in (...)` in `customers.sql` via a Jinja list; finance reconciliation seed. |
| haiku-2 | yes | yes | Same; `customers.sql` change plus a status-to-revenue mapping seed. |
| haiku-3 | yes | yes | Maps `delivered` to `completed` in staging and removes it from both `accepted_values` lists; admin monthly totals as a seed. |

**Verification.** Both fix shapes restore `customers` to the clean values (1103.00), and the staging mapping also restores
`orders.status`. Reverting the two `accepted_values` lists without a model change makes the build fail on 8 rows, which
is a guard, not a fix; the reconciliation against the admin total fails on the broken data and passes clean.

**What it says.** Six of six. Every run asked finance to confirm that `delivered` counts as completed before shipping,
and every run named the widened test list as the reason the build stayed green.

## Opus runs (week 6)

Three runs of `claude-opus-5-5`, same command, prompt assembled by `check_case.py` from the project at f769fdf, before
the week 6 lookup extension, so the same project the other two models saw; verdicts in `results.csv`.

| Run | Root cause | Fix | Notes |
|---|---|---|---|
| opus-1 | yes | yes | `delivered` from March is what the admin counts as completed, both `accepted_values` lists widened with no model change; `delivered` mapped to completed in staging keeping `source_status`, lists restored, reconciliation to the source definition. |
| opus-2 | yes | yes | Same mapping; strict `accepted_values` on the mapped column as the guard. |
| opus-3 | yes | yes | An `is_completed` flag in staging used by `customers` and `orders`, `status` kept as a real state; a status-to-revenue mapping seed with a `relationships` test so a new status cannot be accepted without a decision. |

Opus: 3 of 3 found, 3 of 3 fixed.
