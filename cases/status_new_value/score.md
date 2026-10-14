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

## Standard sample

**Runs:** three per model, week 8, on the standard output sample (the same queries on every case: row counts, the raw
column list, monthly totals of the output tables and ten rows of every table picked by hash); `runs/standard/prompt.md`
is the frozen prompt every run answered and `runs/standard/` holds the responses. `claude -p --setting-sources ""
--model <model> --tools "" --output-format json` (Claude Code 2.1.299), project ad0b16b. Same rubric; Defects is how
many of the case's injected defects the run named as a cause.

| Run | Root cause | Fix | Defects | Notes |
|---|---|---|---|---|
| sonnet-1 | yes | yes | 1 | New `delivered` status from March that the admin counts as completed, both `accepted_values` lists widened so the build stays green; maps `delivered` to `completed` in `stg_orders` and restores both lists; asks finance and the ingestion owner to confirm before shipping. |
| sonnet-2 | yes | yes | 1 | Same cause, `delivered` added to both lists "to make the test pass, but nothing maps it to a meaning"; staging mapping keeping `raw_status`, both lists restored; reconciliation to the raw tables on the finance definition. |
| sonnet-3 | yes | yes | 1 | Source started emitting `delivered` in March, lists widened; `is_completed` flag in `stg_orders` carried into `orders` and used by `customers`; status map seed with a `relationships` test. |
| haiku-1 | yes | yes | 1 | `delivered` emitted by the source and never classified, added to both lists "without anyone deciding whether it counts as completed"; `is_completed` flag in staging used by `customers.sql`; admin monthly totals as a seed. Side note that the four seed tests did not run, an artefact of the models-only log. |
| haiku-2 | yes | yes | 1 | Models count only `completed` while the admin counts `delivered`, both lists accept the value; `status in ('completed', 'delivered')` in `customers.sql`; finance control totals as a seed. |
| haiku-3 | yes | yes | 1 | `delivered` came in with the raw feed and nothing treats it as completed, both lists widened; staging mapping and both lists restored, framed as "Decide with finance that `delivered` means completed for revenue purposes. If it does, normalise it in staging"; strict five-value `accepted_values` as the guard. |
| opus-1 | yes | yes | 1 | New `delivered` status from March 2018, placed from the sample (order 75 delivered, order 63 completed), both lists widened; staging mapping keeping `source_status`, `dbt build -s stg_orders+`, lists restored with a warn-level test on the raw column. |
| opus-2 | yes | yes | 1 | Same cause and mapping with `source_status`, both lists restored; strict `accepted_values` moved to `source_status` so it cannot be relaxed to make a downstream change pass. |
| opus-3 | yes | yes | 1 | Same cause, March placement confirmed from the sample; staging mapping with `source_status`, lists restored, precautionary full refresh of `daily_revenue`; reconciliation to raw on the finance definition. |

**Verification.** Every fix matches a shape in the Verification paragraph above.

**What it says.** Nine of nine found and fixed, the same as the six curated Sonnet and Haiku runs and the three Opus runs. The standard sample handed the runs the status breakdown (`delivered` 8 orders, 136.00 beside `completed` 967.00), and every run subtracted 967 from 1103 and 143 from 279, matched both gaps to that row, and read the `__delivered` suffix on the `accepted_values` test names in the log as the widened list; only opus-1 and opus-3 also used the `stg_orders` sample row (order 75 on 2018-03-18 marked `delivered`) to place the switch in March, the other seven inferred it from the arithmetic and said so. Every run asked finance or the ingestion owner to confirm that `delivered` counts as completed before shipping, as the curated runs did, and four runs (haiku-1, opus-1, opus-2, opus-3) added the unrelated `total_order_amount` doc drift as a side note.
