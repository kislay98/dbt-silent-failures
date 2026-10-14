BLOCKERS | shlok | 2026-10-29
- None blocking. Four things that need a nod:
- **Fix rubric for the timezone type.** All 12 isolated runs on the two timezone cases name the mixed time zones, and
  no fix is complete: every `order_date` comes out right and 16 timestamps (1 to 14 March) stay 5h30 early, because
  nothing in the prompt places the switch before 15 March. I score that "partly", so the strict success rate for the
  type is 0 of 12 on the chart. Need from Kislay: keep it strict (chart as is), or count "every reported number right"
  as a fix (the type goes to 7 of 12 on the two cases).
- **`region_revenue` now inner-joins** orders and regions (a one-line base change). With left joins the null flood
  produced a NULL region that `not_null` caught, so type 4 could not be silent. Clean numbers are unchanged, all 11
  cases re-checked, the fan-out cases unaffected.
- **The quiet fan-out variant is not harder.** The build log still prints `INSERT 102` for the seed and all six runs
  read it. A real harder variant needs the harness to drop seed lines from the log (one flag in `check_case.py`) or a
  single re-sent row. Proposing the flag for week 4 rather than adding it unasked.
- **Decisions 1, 3 and 4 are in.** `prompt.md` holds the symptom, a seeds flag and the queries; `check_case.py`
  assembles the full prompt at run time, checks one key number from `ground_truth.md`, and runs models into `runs/`.
  Case folders are 100 to 160 lines now. Leftover: the week 1 cases' first runs answered the old embedded prompt; every
  later run answered the assembled one and its header says which project commit it ran on.
