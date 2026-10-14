WEEKLY REPORT | shlok | Week 7 | 2026-11-28

## Done
| Task ID | Task | Planned days | Actual days | Checks passed first try (Y/N) | Link (PR, post, chart) |
|---|---|---|---|---|---|
| T1-shlok-37 | Four mixed cases to 32: `late_and_timezone`, `duplicates_and_rename`, `category_and_units`, `fanout_and_late`, each with the second defect out of the complaint; check passes on all 32; 36 runs scored plus nine re-runs on one amended prompt, every fix shape applied to the broken data first | 3 | 3 | N (one prompt amended after its first nine runs, blockers 26 Nov) | `cases/<name>/` |
| T1-shlok-38 | Pilot package: README numbers and chart at 32 cases and 288 runs, a one-page findings summary for the paper, the demo script | 1 | 0.75 | Y | `reports/2026-11-28-pilot-summary.md`, `reports/2026-11-28-pilot-demo.md` |
| T1-shlok-39 | Wed blockers, Fri weekly report; the six first-prompt `rename_and_utc` runs brought into the repo as asked | 0.25 | 0.25 | Y | `reports/2026-11-26-shlok-blockers.md`, this file |

## Not done
| Task ID | Task | Status | Why | Blocked since |
|---|---|---|---|---|
| none | | | | |

## Next week
| Task ID | Task | Definition of done | Planned days |
|---|---|---|---|
| T1-shlok-40 | Pilot on 7 Dec: the demo script run end to end, the summary presented, questions logged | Notes in `reports/` | 0.5 |
| T1-shlok-41 | Fold the pilot feedback in: rubric or prompt changes agreed on the day, re-scored where they bite | Changes in `cases/` and `results.csv` | 2 |
| T1-shlok-42 | Plan for the bigger real-world project after the pilot: candidate projects, what the harness needs (sources instead of seeds, a load timestamp), a week-by-week sketch | `reports/` plan file | 1.5 |
| T1-shlok-43 | Wed blockers, Fri weekly report | Files in `reports/` | 0.25 |

Pace check: 32 cases and every type at four or more with mixed cases counted toward both of their types (the rule Kislay confirmed on 24 Nov); the pilot target is met a week early, so week 8 is the pilot itself and the plan for what follows.

## Reviews I gave
| PR link | Author | Result (approved / changes requested) |
|---|---|---|
| none | none | none (no other author) |

## Numbers
- T1 cases built (total): 32 (4 this week)
- Failure types with at least one case: 8 of 8; at four or more cases (mixed counted toward both of its types): 8 of 8
- T1 models run (total): 3; 288 runs scored (36 this week, plus 9 first-prompt runs kept outside `results.csv`); about USD 4 this week, USD 30 in total
- Root cause found: 255 of 288 (Sonnet 89 of 96, Haiku 77 of 96, Opus 89 of 96)
- Complete fix: 237 of 288 (Sonnet 81 of 96, Haiku 72 of 96, Opus 84 of 96)
- The new cases: found 21 of 36, fixed 19 of 36; the mixed type stands at 73 of 99 found, 69 of 99 fixed (Sonnet 28 / 24, Haiku 18 / 18, Opus 27 / 27 of 33)
- T2 / T3 shipped this week: none (paused)

## Demo on Friday
The pilot script, `reports/2026-11-28-pilot-demo.md`, run once as a rehearsal: `uv run --project jaffle_shop check_case.py` (32 PASS lines, 44 tests each side), the chart, then `cases/duplicates_and_rename/runs/` (nine runs that saw the copied payment and nine that handed it back), `cases/late_and_timezone/score.md` (the two prompt versions side by side), and `cases/category_and_units/runs/` (the 1.98 residue chased by five runs and dropped by four).

## Shareable win
32 cases, three models, 288 runs, every type at four or more. Three results this week for the paper. First, where the second
defect sits decides the score more than which model runs: with both defects in the complaint the mixed cases are 9 of 9;
with the second only in the sample, a regional total 101.00 over `orders` is acted on by 9 of 9 (`fanout_and_late`), but a
copied payment row in a listing is seen by 9 of 9 and acted on by none (`duplicates_and_rename`, "may be a legitimate
split payment"); behind the first fix, a 1.98 residue is chased by 5 of 9 (`category_and_units`) and a hidden status by
3 of 9 (`rename_and_new_status`). A total that must reconcile gets fixed; a row that might be legitimate gets handed
back. Second, one clause in the prompt is worth more than a model tier on the timezone cases: `late_and_timezone` went
from 3 of 9 to 7 of 9 when the prompt said the old rows had reconciled, and `rename_and_utc` from 0 of 6 to 5 of 6 on
the two smaller models; Opus was 3 of 3 with and without the clause, which is the one place the larger model separates.
Third, across all 288 runs the larger model now leads on fixes, 84 to Sonnet's 81 and Haiku's 72 of 96, and the whole
lead is on the mixed cases (27 to 24 to 18 of 33); on the eight single-type families the three are level.
