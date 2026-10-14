WEEKLY REPORT | shlok | Week 6 | 2026-11-21

## Done
| Task ID | Task | Planned days | Actual days | Checks passed first try (Y/N) | Link (PR, post, chart) |
|---|---|---|---|---|---|
| T1-shlok-32 | Opus (`claude-opus-5-5`) three times on every case: 63 runs on the 21 existing cases from the project version the other two models saw, 21 on the new ones; the harness gained a 300 s per-call timeout after two calls hung | 1 | 1 | N (hung calls re-run) | `results.csv`, `reports/success_by_type.svg` |
| T1-shlok-34 | Second extension (`raw_payment_methods` joined inside `orders` for `fees`; clean totals unchanged, 40 to 44 tests) and seven cases: `payment_methods_fanout`, `processor_history` (type 2), `fanout_and_nulls`, `duplicates_and_units`, `rename_and_new_status`, `rename_and_utc`, `late_and_new_status` (mixed); check passes on all 28; 63 runs scored, every fix shape applied to the broken data | 4 | 3.5 | N (one prompt amended after its first six runs, blockers 19 Nov) | `cases/<name>/` |
| T1-shlok-36 | Wed blockers, Fri weekly report | 0.25 | 0.25 | Y | `reports/2026-11-19-shlok-blockers.md`, this file |

## Not done
| Task ID | Task | Status | Why | Blocked since |
|---|---|---|---|---|
| T1-shlok-35 | Second check mode for the spares | dropped | Kislay left soft deletes and stale sources out of the pilot (14 Nov) | n/a |

## Next week
| Task ID | Task | Definition of done | Planned days |
|---|---|---|---|
| T1-shlok-37 | Four more mixed cases to 32, built so the second defect hides behind the first: candidates `late_and_timezone` (February orders arriving late and in UTC), `duplicates_and_rename` (replayed payments behind the method rename), `category_and_units` (`delivered` plus coupon dollars), `fanout_and_late` (a lookup history row plus late payments) | Folders, check passes, three runs per model scored and verified | 3 |
| T1-shlok-38 | Pilot package: README numbers frozen, chart, a one-page summary of the findings for the paper, the demo script | Files in `reports/` | 1 |
| T1-shlok-39 | Wed blockers, Fri weekly report | Files in `reports/` | 0.25 |

Pace check: 28 cases after week 6 and every type at four or more with mixed cases counted toward their components (19 Nov
note); four more in week 7 reach 32, and week 8 is the pilot.

## Reviews I gave
| PR link | Author | Result (approved / changes requested) |
|---|---|---|
| none | none | none (no other author) |

## Numbers
- T1 cases built (total): 28 (7 this week)
- Failure types with at least one case: 8 of 8; at four or more cases (mixed counted toward its components): 8 of 8
- T1 models run (total): 3; 252 runs scored (126 this week: 63 Opus on the existing cases, 63 on the new ones); about USD 21 this week (Opus about USD 16), USD 26 in total
- Root cause found: 234 of 252 (Sonnet 81 of 84, Haiku 73 of 84, Opus 80 of 84)
- Complete fix: 218 of 252 (Sonnet 75 of 84, Haiku 68 of 84, Opus 75 of 84)
- The new cases: found 55 of 63, fixed 55 of 63; the mixed type now stands at 52 of 63 found, 50 of 63 fixed; timezone shift at 24 of 27 found, 9 of 27 fixed
- T2 / T3 shipped this week: none (paused)

## Demo on Friday
`uv run --project jaffle_shop check_case.py` (28 PASS lines, 44 tests each side), then `cases/units_and_rename/score.md`
(Opus three for three where Sonnet fixed one and Haiku found neither), then `cases/rename_and_new_status/runs/` (three
runs that mapped `delivered` and six, Opus included, that handed the value back to finance), then
`cases/rename_and_utc/score.md` next to `cases/utc_switch/score.md` (the rename marks the cut-over that the timezone case
alone never gives).

## Shareable win
28 cases, three models, 252 runs. Three results for the paper. First, the larger model does not separate overall:
Opus is 80 of 84 found and 75 of 84 fixed against Sonnet's 81 and 75 of 84, and on the eight
single-type families all three models sit at or near 100%. Where Opus wins it is on inference under ambiguity
(`units_and_rename` 3 of 3 fixed against Sonnet's 1 and Haiku's 0; the blind timezone prompt 3 of 3 found against 1
and 2), and where it loses it is by refusing to infer: on `rename_and_new_status` it is 0 of 3 against Sonnet's 2,
because it writes the query that would settle what `delivered` means and stops there. Second, "two bugs together are
harder" depends on whether the second bug is in the sample: `fanout_and_nulls` and `late_and_new_status` are 9 of 9
because both defects show in the queries, while `rename_and_new_status`, whose second defect appears only after the
first is fixed, is 3 of 9 found. Third, a mixed case can be easier than its part: `rename_and_utc` is 8 of 9 fixed
against 0 of 18 on the two `utc_switch` cases, because the rename marks the cut-over that the timezone shift alone
never reveals.
