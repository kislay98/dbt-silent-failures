# Pilot demo script (7 December 2026, 25 minutes)

Rehearsed on 28 November. Run from the repo root on a clean checkout. The full check takes about 20 minutes, so start it
before the slot and show the finished log.

1. **The claim (1 min).** 32 cases where `dbt build` passes with every test green and an output table is wrong; 8
   failure types, 3 models, 288 runs, two yes/no verdicts per run.
2. **One case by hand (5 min).**
   ```bash
   cd jaffle_shop && uv run dbt build          # clean: 44 pass, sum(orders.amount) = 1672
   cd .. && python3 cases/duplicate_payments/inject.py
   cd jaffle_shop && uv run dbt build          # broken: 44 pass, sum(orders.amount) = 1852
   uv run python -c "import duckdb; print(duckdb.connect('jaffle_shop.duckdb').sql('select sum(amount) from orders'))"
   git checkout seeds/raw_payments.csv && cd ..
   ```
   Then `cases/duplicate_payments/ground_truth.md`: what broke, where it shows, why nothing fails, the key number.
3. **The harness (3 min).** The finished `uv run --project jaffle_shop check_case.py` log: 32 PASS lines, 44 tests each
   side, the key number on both sides. Then `uv run --project jaffle_shop check_case.py cases/late_and_timezone --prompt | head -60`:
   what a model sees, and nothing else.
4. **The chart (2 min).** `reports/success_by_type.svg`: found and fixed by type and model. The timezone column and the
   mixed column carry the story.
5. **Three cases that carry the findings (10 min).**
   - `cases/duplicates_and_rename/runs/opus-3.md`: the copied payment seen, the dedupe written, then "only if a
     legitimate repeat payment of the same amount can't happen". Nine runs, nine hand-backs; `score.md`, "What it says".
   - `cases/late_and_timezone/score.md`: two tables, 3 of 9 and 7 of 9, with `runs/first_prompt/prompt.md` next to
     `prompt.md` (one clause apart).
   - `cases/category_and_units/runs/opus-2.md` against `runs/haiku-2.md`: the 1.98 chased to order 76, against a run
     that never read the `delivered` rows.
6. **Numbers and caveats (2 min).** `reports/2026-11-28-pilot-summary.md`.
7. **Questions (2 min).** Logged in `reports/2026-12-07-pilot-notes.md` on the day.
