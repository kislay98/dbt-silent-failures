WEEKLY REPORT | shlok | Week 3 | 2026-10-31

## Done
| Task ID | Task | Planned days | Actual days | Checks passed first try (Y/N) | Link (PR, post, chart) |
|---|---|---|---|---|---|
| T1-shlok-20 (carried) | Kislay's decisions 1, 3 and 4: `check_case.py` assembles each prompt at run time from `prompt.md` (symptom, seeds flag, queries), checks one key number from `ground_truth.md`, runs models with `--run <model> --times N` into `runs/`; all 11 prompts converted and regenerated, diffed against the old embedded ones | 1 | 1 | N (the first assembly read the project files before the injection, so `status_title_case` lost its widened lists; the diff caught it. DuckDB's renderer cut tables at 20 rows; the harness renders every row now) | `check_case.py`, `cases/*/prompt.md` |
| T1-shlok-20b | Three runs per model per case, 66 runs, every proposed fix and test verified against the data, per-run verdicts in each `score.md` and in `results.csv` | 1 | 1.5 | Y | `results.csv`, `cases/*/runs/`, `cases/*/score.md` |
| T1-shlok-21 | Case `join_drops_rows` (type 4, null flood); needed `region_revenue` on inner joins (blockers, 29 Oct) | 0.75 | 0.75 | Y | `cases/join_drops_rows/` |
| T1-shlok-22 | Harder variants: `utc_switch_blind` (no example order; the shop's country and the admin screen's daily counts instead) and `region_fanout_quiet` (2 re-sent rows) | 1 | 0.75 | N (the quiet variant turned out no harder, the seed row count is still in the log) | `cases/utc_switch_blind/`, `cases/region_fanout_quiet/` |
| T1-shlok-23 | Mixed case `late_and_duplicate` (types 6 and 1 in one load) | 0.75 | 0.5 | Y | `cases/late_and_duplicate/` |
| T1-shlok-24 | `results.csv` (one row per case, model and run) and `reports/chart.py`, which draws `reports/success_by_type.svg` with no new dependency; palette validated | 0.75 | 0.5 | Y | `reports/success_by_type.svg` |
| T1-shlok-25 | Wed blockers, Fri weekly report | 0.25 | 0.25 | Y | `reports/2026-10-29-shlok-blockers.md`, this file |

## Not done
| Task ID | Task | Status | Why | Blocked since |
|---|---|---|---|---|
| none | none | none | none | none |

## Next week
| Task ID | Task | Definition of done | Planned days |
|---|---|---|---|
| T1-shlok-26 | Harness: a `Log: models only` line in `prompt.md` that drops seed lines from the build log; re-run `region_fanout_quiet` with it | Six new runs scored; the variant is measurably harder or we drop it | 0.5 |
| T1-shlok-27 | Apply Kislay's fix-rubric decision to the two timezone cases, `results.csv` and the chart | Scores and chart consistent with the decision | 0.25 |
| T1-shlok-28 | Five cases toward four per type: `duplicate_orders` (type 1, orders re-sent with fresh ids), `coupon_cents` (type 7, half the coupon rows switch units), `payment_method_renamed` (type 3, in the payments feed), `late_payments` (type 6, payments only), `status_new_value` (type 8, a new status with the list widened) | Five folders, check passes, three runs per model scored | 3.5 |
| T1-shlok-29 | Wed blockers, Fri weekly report | Files in `reports/` | 0.25 |

Pace check: 11 cases after week 3, 32 needed by week 8, so 21 more over five weeks, about four a week at the week 3 cost
of roughly half a day each with the harness doing the runs.

## Reviews I gave
| PR link | Author | Result (approved / changes requested) |
|---|---|---|
| none | none | none (no other author) |

## Numbers
- T1 cases built (total): 11 (4 this week)
- Failure types with at least one case: 8 of 8, plus one mixed case
- Types at four cases: 0 (join fan-out and timezone shift at 2; the mixed case is a second for late data and duplicates)
- T1 models run (total): 2, three runs each on every case, 66 runs, about USD 2.5 in total (Sonnet about 0.06 a run, Haiku 0.005)
- Runs right on root cause and fix: 53 of 66 (Sonnet 27 of 33, Haiku 26 of 33); every miss is on the timezone cases except one Haiku run on the null flood
- T2 / T3 shipped this week: none (paused)

## Demo on Friday
`uv run --project jaffle_shop check_case.py` (eleven PASS lines with their key numbers), then `reports/success_by_type.svg`,
then `cases/utc_switch_blind/runs/`: six answers to the same prompt, three of which explain away why January agreed.

## Shareable win
66 isolated runs over 11 cases and all 8 failure types. Both models find and fix everything except the timezone switch,
where all 12 runs name the mixed zones, none can place the switch, and four would have broken January. Two findings for
the paper on the way: dbt's `relationships` test skips NULL keys, so a null flood only trips `not_null`; and a `NOT IN`
subquery on a NULL key returns nothing, which is exactly how one Haiku run talked itself out of the right answer.
