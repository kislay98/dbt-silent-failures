WEEKLY REPORT | shlok | Week 5 | 2026-11-14

## Done
| Task ID | Task | Planned days | Actual days | Checks passed first try (Y/N) | Link (PR, post, chart) |
|---|---|---|---|---|---|
| T1-shlok-30 | Models-only log from a real `dbt build --exclude resource_type:seed` (34 nodes numbered from 1, no filtered lines); each run header now carries the sha256 of its prompt; the five week 4 score files note which form their runs saw | 0.25 | 0.25 | Y | `check_case.py` |
| T1-shlok-31 | Six cases: `regions_fanout_history` (type 2), `null_order_dates` and `null_payment_amounts` (type 4), `utc_reexport` (type 5), `transfers_in_micros` (type 7), `units_and_rename` (mixed, types 7 and 3); seeds hidden and models-only log on all six; check passes; 36 runs scored, every fix and test applied to the broken data | 3.5 | 3 | Y (two cases swapped against the plan for cause, blockers 12 Nov) | `cases/<name>/` |
| T1-shlok-33 | Wed blockers, Fri weekly report | 0.25 | 0.25 | Y | `reports/2026-11-12-shlok-blockers.md`, this file |

## Not done
| Task ID | Task | Status | Why | Blocked since |
|---|---|---|---|---|
| T1-shlok-32 | Opus as a third model on every case | not started | needs approval of the spend (about USD 5 for one run per case, USD 15 for three) | 2026-11-07 |

## Next week
| Task ID | Task | Definition of done | Planned days |
|---|---|---|---|
| T1-shlok-34 | Seven cases toward four per type (two each for duplicate rows, schema drift, late data and category change; the two fan-out cases if the extension is approved, else two more mixed). Candidates: `duplicate_customers_feed` and `payments_batch_twice` (type 1), `amount_as_text` and `status_substatus_split` (type 3), `late_payments_window` and `late_orders_rolling` (type 6), `status_split_values` and `status_codes` (type 8), `payment_fees_fanout` and `products_fanout` (type 2); the final seven in Monday's plan | Folders, check passes, three runs per model scored and verified | 4 |
| T1-shlok-35 | Second check mode for the spares if approved: `ground_truth.md` states the expected output, the check verifies the actual output did not move | `check_case.py` passes a `soft_deleted_orders` case | 0.5 |
| T1-shlok-36 | Wed blockers, Fri weekly report | Files in `reports/` | 0.25 |

Pace check: 21 cases after week 5, 32 needed by week 8 and four per type means 13 more (two each for duplicate rows,
join fan-out, schema drift, late data and category change, one each for null flood, timezone shift and unit change).
Seven in week 6 and six in week 7 leaves week 8 for the pilot.

## Reviews I gave
| PR link | Author | Result (approved / changes requested) |
|---|---|---|
| none | none | none (no other author) |

## Numbers
- T1 cases built (total): 21 (6 this week)
- Failure types with at least one case: 8 of 8, plus two mixed cases
- Types at four cases: 0; at three: null flood, timezone shift, unit change; at two: duplicate rows, join fan-out, schema drift, late data, category change
- T1 models run (total): 2, three runs each on every case, 126 runs scored (36 this week); about USD 1.4 this week, USD 5.1 in total
- Root cause found: 117 of 126 (Sonnet 61 of 63, Haiku 56 of 63)
- Complete fix: 106 of 126 (Sonnet 55 of 63, Haiku 51 of 63)
- This week: found 32 of 36, fixed 30 of 36; the misses are all on the mixed case (3 of 6 found, 1 of 6 fixed) plus one Haiku arithmetic slip on the micro-units and one Haiku fix that covered four of eight NULL amounts
- T2 / T3 shipped this week: none (paused)

## Demo on Friday
`uv run --project jaffle_shop check_case.py` (twenty-one PASS lines), then `cases/utc_reexport/score.md` next to
`cases/utc_switch/score.md` (the same conversion is the right fix in one and the wrong fix in the other), then
`cases/units_and_rename/runs/`: three Haiku runs that explain the second defect away as orphan payments, and two
Sonnet runs that find it and write a rule that turns an 18.00 coupon into 1800.00.

## Shareable win
21 cases, 126 runs. Two results for the paper. First, the timezone type is not unfixable: with the whole history
shifted (`utc_reexport`) both models fix it 6 of 6, because the blanket conversion that broke January in the switch
cases is the right answer when there is no boundary; the type now stands at 15 of 18 found, 6 of 18 fixed, and the
difference is exactly whether a cut-over has to be placed. Second, a mixed case is harder than its parts: the rename
and the coupon dollars score 12 of 12 and 11 of 12 on their own and 3 of 6 found, 1 of 6 fixed together. The second
defect hides behind the first, and the two Sonnet runs that found both over-generalised the fix to the one cents
coupon left after the rename.
