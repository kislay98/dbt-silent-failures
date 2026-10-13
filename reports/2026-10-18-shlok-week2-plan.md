WEEK 2 PLAN | shlok | 2026-10-18

I own Track 1 (cases and harness) from this week. Target: pilot in week 8 (7 Dec) with 32 cases (4 per failure type),
2-3 models on every case, one chart of success rate by failure type and model. Week 2 is 20-24 Oct: blockers Wed 22,
weekly report Fri 24. Every change is a PR with a Claude review; every case passes `check_case.py`; every `score.md`
names the model id from `claude -p --output-format json` (`modelUsage` key, e.g. `claude-sonnet-5-5`).

## Tasks
| Task ID | Task | Definition of done | Planned days |
|---|---|---|---|
| T1-shlok-11 | Automated case check: `check_case.py` (clean build green, `inject.py` changes a model's output, build still green after it) | Both week 1 cases pass; a non-silent injection fails; README documents it; PR merged (drafted 17 Oct, PR Mon) | 0.5 |
| T1-shlok-12 | Grow `jaffle_shop` once so the remaining types have something to break: `ordered_at` timestamp on orders, `raw_customer_regions` dimension joined inside the lifetime-value aggregate, a `completed_revenue` metric filtered on status, `daily_revenue` incremental with a 3-day lookback | One PR; both week 1 cases still pass the check; README clean numbers and test count updated (drafted 17 Oct, PR after T1-shlok-11) | 1 |
| T1-shlok-13 | Case `region_fanout` (type 2, join fan-out) | Five files, check passes, seeds hidden in the prompt, Sonnet run and scored with model id | 0.75 |
| T1-shlok-14 | Case `status_renamed` (type 3, schema drift) | same | 0.75 |
| T1-shlok-15 | Case `utc_switch` (type 5, timezone shift) | same | 0.75 |
| T1-shlok-16 | Case `late_orders` (type 6, late-arriving data) | same | 0.75 |
| T1-shlok-17 | Case `status_title_case` (type 8, category value change) | same | 0.75 |
| T1-shlok-18 | DataClawEval task list (arXiv 2607.28033) | Note below | 0.25 (done) |
| T1-shlok-19 | Wed blockers, Fri weekly report | Files in `reports/` | 0.25 |

Total 5.75 days against 5. If anything slips it is T1-shlok-17, to Mon 27 Oct. Stretch if there is time: T1-shlok-10
from last week (second model, Claude Haiku, on the two week 1 cases), 0.5 day.

## Case designs
All five inject data only; the project changes come once in T1-shlok-12. Prompts give the models, schema tests, the
build log, output samples and a column listing of the raw tables, not the seeds (Kislay, 17 Oct: hide seeds). Numbers in the
"what the prompt shows" column are design targets, fixed when each case is built.

| Case | Type | Injection | Why `dbt build` stays green | What the prompt shows | What makes it hard |
|---|---|---|---|---|---|
| `region_fanout` | 2 | The regions feed re-sends 8 customers' rows with a new `updated_at` and the same region | Fan-out happens in the join before `group by customer_id`, so `customers` keeps one row per customer; the dimension key has no `unique` test | `sum(customer_lifetime_value)` is 190 above `sum(orders.amount)`, 8 customers doubled | Row counts are right everywhere; no row is visibly duplicated in the output |
| `status_renamed` | 3 | From 2018-03-15 the orders feed carries `order_status` and sends `status` empty | `accepted_values` ignores NULL; nothing tests `not_null` on status | March `completed_revenue` falls to a third; `orders.status` NULL from a date | The new column is visible only in the raw column listing; the model must link the NULL pattern to a schema change |
| `utc_switch` | 5 | From 2018-03-01 `ordered_at` arrives in UTC instead of IST (5h30 earlier) | No test can check timestamp meaning; `order_date` is derived by a cast | Daily counts disagree with the ops dashboard; some 1 March orders land on 28 Feb | Only the hour-of-day distribution gives it away |
| `late_orders` | 6 | 6 February orders arrive in the April batch | `orders` and `customers` read the seed and include them; `daily_revenue` is incremental with a 3-day lookback and never will | Feb revenue in `daily_revenue` is 30 below Feb `sum(orders.amount)` | Two models disagree and both pass; the model must read the incremental filter, not the data |
| `status_title_case` | 8 | App release writes `Completed`; the on-call widened `accepted_values` to stop the alert (injected as the data plus the schema.yml edit) | The widened test passes; `completed_revenue` filters on `status = 'completed'` and drops the new rows | April completed revenue drops to zero | Mixed failure: a data change plus a test change that hides it. A plain `COMPLETED` fails `accepted_values` (checked), so on this project the type is only silent with the widened test or on an untested column |

Type 4 (null flood, `join drops rows`, T1-shlok-09) and a harder type 7 variant (T1-shlok-08) move to week 3; week 3
also starts mixed cases (e.g. late rows plus duplicates) so every type reaches 4 cases by week 8.

## Decisions I need from Kislay
1. PR size. A case PR is 700+ lines because `prompt.md` embeds the project files. Proposal: `prompt.md` keeps only the
   symptom, instructions and the list of files it includes, and `check_case.py` assembles the full prompt from the
   project at run time. Case PRs drop to about 200 lines and hiding seeds becomes one line. Alternative: exempt case PRs.
2. Growing `jaffle_shop` once (T1-shlok-12) instead of each `inject.py` editing models. Week 1 ground truths keep
   their 28/28 numbers as written; each `score.md` records the project commit it ran against.
3. The check's definition of pass: clean build green, injection changes at least one model's output, build still green.
   It does not verify the numbers in `ground_truth.md`; those stay a human check in review.

## DataClawEval (arXiv 2607.28033), read 17 Oct
No task is a silent data failure like ours. All 100 tasks ask the agent to build a pipeline from a natural-language
request (5 engines: PySpark, MySQL, HiveSQL, Presto/Trino, FlinkSQL; 5 business domains) and grade the output table
with a per-task `grade.py`. None hands the agent a pipeline that runs green with wrong output. Two things are useful
for our paper: (a) Appendix B finds that of 292 low-scoring agent runs, 102 are "silent": the program finishes and
writes an output table, but the result is wrong. Agents produce silent failures a third of the time; nobody measures
whether they can detect them, which is our gap. (b) Their task validation is our check in reverse: experts perturb the
reference code and the synthetic inputs must produce a different output from the perturbed version. Repo:
https://github.com/Dicemy/DataClawEval (task.md + init/ + gt/grade.py per task, no bug-fix or data-quality tasks).

## Paused
Open-source tool and reproduction series (Kislay, 17 Oct).
