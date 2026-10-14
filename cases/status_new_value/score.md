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
