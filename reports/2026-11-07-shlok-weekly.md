WEEKLY REPORT | shlok | Week 4 | 2026-11-07

## Done
| Task ID | Task | Planned days | Actual days | Checks passed first try (Y/N) | Link (PR, post, chart) |
|---|---|---|---|---|---|
| T1-shlok-27 | Rubric: two yes/no numbers per run (root cause found, complete fix); `results.csv` mapped from the two columns it already had, three score files updated, chart redrawn as two panels on one scale, README table in found / fixed | 0.25 | 0.25 | Y | `results.csv`, `reports/success_by_type.svg` |
| T1-shlok-26 | `Log: models only` line in `prompt.md`; `check_case.py` drops the seed lines from the build log; `region_fanout_quiet` re-run six times | 0.5 | 0.5 | N (the flag works; the variant was no harder, 6 of 6 again, so it is dropped per the definition of done; blockers 5 Nov) | `check_case.py` |
| T1-shlok-28 | Five cases: `duplicate_orders` (type 1), `coupon_cents` (type 7, half the coupon rows), `payment_method_renamed` (type 3), `late_payments` (type 6), `status_new_value` (type 8); seeds hidden and seed lines dropped on all five; check passes; 30 runs scored, every fix and test applied to the broken data | 3.5 | 3 | Y | `cases/<name>/` |
| T1-shlok-29 | Wed blockers, Fri weekly report | 0.25 | 0.25 | Y | `reports/2026-11-05-shlok-blockers.md`, this file |

## Not done
| Task ID | Task | Status | Why | Blocked since |
|---|---|---|---|---|
| none | none | none | none | none |

## Next week
| Task ID | Task | Definition of done | Planned days |
|---|---|---|---|
| T1-shlok-30 | Models-only log from a real `dbt build --exclude resource_type:seed` in the harness, so the log has no numbering gap | `--prompt` on a `Log: models only` case shows a log numbered from 1 with no seed nodes; existing runs untouched | 0.25 |
| T1-shlok-31 | Six cases toward four per type, starting with the types at one: `payments_fanout` (type 2, a payment-method lookup with a duplicate key joined in `orders`), `regions_fanout_history` (type 2, a real region change, two rows with different regions), `null_order_dates` (type 4, `ordered_at` empty for a batch, `order_date` has no `not_null`), `null_regions_new_customers` (type 4, no region row for customers after a date, the inner join drops them), `dst_end` (type 5, the feed follows Australian DST and the warehouse does not, orders near midnight move from 1 April), `units_and_rename` (mixed, types 7 and 3 in one load) | Six folders, check passes, three runs per model scored and verified | 3.5 |
| T1-shlok-32 | Third model, if approved: `claude-opus-5-5` once on every case (about USD 5 for 15 cases), or three times (about USD 15) | Runs saved, `results.csv` and chart carry a third series | 0.5 |
| T1-shlok-33 | Wed blockers, Fri weekly report | Files in `reports/` | 0.25 |

Pace check: 15 cases after week 4, 32 needed by week 8. Week 8 is the pilot itself, so 17 cases over weeks 5 to 7 is about
six a week, against five this week at roughly half a day each with the harness doing the runs.

## Reviews I gave
| PR link | Author | Result (approved / changes requested) |
|---|---|---|
| none | none | none (no other author) |

## Numbers
- T1 cases built (total): 15 (5 this week, 1 dropped: `region_fanout_quiet`)
- Failure types with at least one case: 8 of 8, plus one mixed case
- Types at four cases: 0; at two: duplicate rows, schema drift, timezone shift, late data, unit change, category change; at one: join fan-out, null flood, mixed
- T1 models run (total): 2, three runs each on every case, 90 runs scored (30 this week, plus 6 on the dropped variant); about USD 1.2 this week, USD 3.7 in total
- Root cause found: 85 of 90 (Sonnet 43 of 45, Haiku 42 of 45)
- Complete fix: 76 of 90 (Sonnet 39 of 45, Haiku 37 of 45); every missed fix is on the two timezone cases except one Haiku run each on null flood and unit change
- T2 / T3 shipped this week: none (paused)

## Demo on Friday
`uv run --project jaffle_shop check_case.py` (fifteen PASS lines with their key numbers), then `reports/success_by_type.svg`
with the two panels, then `cases/coupon_cents/runs/haiku-2.md` next to `haiku-1.md`: the same prompt, one run reads the
0.02 coupon rows as shrunken, the other reads the gap as rows that never arrived.

## Shareable win
15 cases over all 8 types and 90 isolated runs, now scored on two numbers. Hiding the seeds and stripping the seed row
counts from the log changed nothing in the result: every run on the five new cases reconstructed the broken rows from the
output tables alone, down to the id offset of a replayed batch. The misses are direction errors, not mechanism errors:
each of the two Haiku misses outside the timezone type read a gap as rows that never arrived (orders missing, payments
orphaned) when the rows were there with the wrong values or the wrong key. Both would have been caught by the run's own
confirmation query, which the harness does not let it run. The timezone switch is still the one failure neither model
can fix from a prompt that does not place the switch.
