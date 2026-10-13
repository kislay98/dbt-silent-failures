WEEKLY REPORT | shlok | Week 2 | 2026-10-24

## Done
| Task ID | Task | Planned days | Actual days | Checks passed first try (Y/N) | Link (PR, post, chart) |
|---|---|---|---|---|---|
| T1-shlok-11 | Automated case check `check_case.py`: clean build green, injection changes a model's output, build still green; seeds reloaded in full after the injection, models run incrementally | 0.5 | 0.5 | N (first version restored the project with `git checkout`, which refused to run on the uncommitted base-project change; switched to a copy-aside) | `check_case.py`, PR 1 |
| T1-shlok-12 | Grow `jaffle_shop`: `ordered_at` timestamps, `raw_customer_regions` + `region_revenue`, `customers.completed_lifetime_value`, `daily_revenue` incremental with a 3-day lookback; 28 to 40 tests, clean totals unchanged, both week 1 cases still pass | 1 | 1 | Y | `jaffle_shop/`, PR 2 |
| T1-shlok-13 | Case `region_fanout` (type 2), Sonnet run and scored | 0.75 | 0.5 | Y | `cases/region_fanout/`, PR 3 |
| T1-shlok-14 | Case `status_renamed` (type 3), Sonnet run and scored | 0.75 | 0.75 | N (a seed with a new column does not load without `dbt seed --full-refresh`, the check now does that; the first cut-over hit only 3 completed orders, moved to 1 March) | `cases/status_renamed/`, PR 4 |
| T1-shlok-15 | Case `utc_switch` (type 5), Sonnet run and scored: first miss | 0.75 | 0.75 | N (first prompt quoted month totals, which do not move; regenerated around the days that do) | `cases/utc_switch/`, PR 5 |
| T1-shlok-16 | Case `late_orders` (type 6), Sonnet run and scored | 0.75 | 0.5 | Y | `cases/late_orders/`, PR 6 |
| T1-shlok-17 | Case `status_title_case` (type 8), Sonnet run and scored | 0.75 | 0.5 | Y | `cases/status_title_case/`, PR 7 |
| T1-shlok-18 | DataClawEval task list read: no silent-failure task; a third of their failed agent runs are silent | 0.25 | 0.25 | Y | `reports/2026-10-18-shlok-week2-plan.md` |
| T1-shlok-10 | Stretch: Claude Haiku on `duplicate_payments` and `gift_card_dollars`, scored | 0.5 | 0.25 | Y | `response_haiku.md` and `score.md` in both cases, PR 8 |
| T1-shlok-19 | Wed blockers, Fri weekly report | 0.25 | 0.25 | Y | `reports/2026-10-22-shlok-blockers.md`, this file, PR 9 |
| T1-shlok-20 | Isolate the model runs (`claude -p --setting-sources ""`), re-run all 7 cases on Sonnet and Haiku (14 runs), re-verify every fix and test, rewrite every `score.md` with the confounded verdict kept for comparison | 1.5 (planned for week 3) | 0.75 | Y | `cases/*/score.md`, `cases/*/response*.md`, PR 8 |

## Not done
| Task ID | Task | Status | Why | Blocked since |
|---|---|---|---|---|
| none | none | none | none | none |

## Next week
| Task ID | Task | Definition of done | Planned days |
|---|---|---|---|
| T1-shlok-20b | Run every case three times per model to separate sampling noise from the harness change (the `utc_switch` verdict moved between the confounded and the isolated run) | Three responses per model per case, pass rates in `results.csv` | 1 |
| T1-shlok-21 | Case `join_drops_rows` (type 4, null flood), carried from T1-shlok-09 | Five files, check passes, both models scored | 0.75 |
| T1-shlok-22 | Harder variants: `utc_switch` without the example order in the prompt, `region_fanout` with the seed row count stripped from the log | Two case folders, both models scored | 1 |
| T1-shlok-23 | Mixed case: late rows plus duplicates (types 6 and 1) | Case folder, both models scored | 0.75 |
| T1-shlok-24 | First cut of the pilot chart, success rate by failure type and model, from a new `results.csv` with one row per case and model | `results.csv`, script and PNG in `reports/` | 0.75 |
| T1-shlok-25 | Wed blockers, Fri weekly report | Files in `reports/` | 0.25 |

## Reviews I gave
| PR link | Author | Result (approved / changes requested) |
|---|---|---|
| none | none | none (no other author since 17 Oct) |

## Numbers
- T1 cases built (total): 7 (5 this week)
- T1 cases verified by the other intern (total): 1 (no other intern since 17 Oct; every case passes `check_case.py` instead)
- T1 models run (total): 2 (Sonnet and Haiku on all 7 cases, isolated runs)
- Failure types with at least one case: 7 of 8 (null flood is next)
- T2 / T3 shipped this week: none (paused)

## Demo on Friday
Run `uv run --project jaffle_shop check_case.py` (seven PASS lines in about a minute), then open `cases/utc_switch/`:
the prompt, both models naming the mixed time zones, Sonnet's fix that gets every date right and 16 times of day
wrong, and the earlier confounded run that explained the offset away and broke January.

## Shareable win
Five new silent-failure types in one week, every case checked by one script, both models run on all seven with the
harness isolated from this machine's Claude Code setup. Sonnet and Haiku each solve six of seven; the timezone switch is
the hard one, where both name the mixed zones but neither fix is complete. Haiku matches Sonnet at a tenth of the cost.
