BLOCKERS | shlok | 2026-11-26
- None blocking. Kislay's three answers (24 Nov) are in: a mixed case counts toward both of its types, the four mixed
  cases and the pilot package go ahead, the six first-prompt `rename_and_utc` runs stay in for the paper. Four notes:
- **Four cases built, 32 in all.** `late_and_timezone`, `duplicates_and_rename`, `category_and_units` and
  `fanout_and_late`, each with the second defect out of the complaint: in the sample only (a copied payment in a
  listing, a regional report 101.00 over `orders`, six backfilled dates against the store's list) or behind the first
  fix (a 1.98 residue after the status mapping). Check passes on all 32 with 44 tests each side; every fix shape was
  applied to the broken data in the isolated copy before any model answered. 36 runs plus a re-run of nine, about USD 4;
  no hung calls this week.
- **The first-prompt runs are in the repo.** `cases/rename_and_utc/runs/first_prompt/` holds the six runs and the prompt
  they answered, scored 0 of 6 in the score file (every run read the whole feed as UTC and converted every row). They are
  not in `results.csv`, and the README says so.
- **The same prompt gap, caught again.** `late_and_timezone`'s first prompt said ops reconciles `order_date` against the
  admin's local time but not that the two had agreed before the load. All six Sonnet and Haiku runs called the shifted
  backfill "timezone, not a bug"; the three Opus runs inferred the local convention from the reconciliation. I added the
  clause from `rename_and_utc` ("and until this load the two agreed on every day since January"), kept the nine runs in
  `runs/first_prompt/` with that prompt, and re-ran: 7 of 9 (Sonnet 3, Haiku 1, Opus 3). Both versions are in the repo,
  so the paper can report the A/B: one clause, 3 of 9 to 7 of 9.
- **Pilot package.** `reports/2026-11-28-pilot-summary.md` (one page for the paper) and
  `reports/2026-11-28-pilot-demo.md` (the script for the 7 Dec slot), with the README numbers and the chart at 32 cases
  and 288 runs. Say if the summary should live in a doc rather than the repo.
