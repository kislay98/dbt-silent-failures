# Pilot demo script (25 minutes)

Run from the repo root on a clean checkout. The full check takes about 20 minutes, so start it before the slot and show
the finished log.

1. **The claim (1 min).** 32 cases where `dbt build` passes with every test green and an output table is wrong; 8
   failure types, 3 models, 288 runs on one fixed output sample (and the same 288 on hand-picked queries, kept as the
   comparison), two yes/no verdicts per run.
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
   side, the key number on both sides, "frozen prompt: matches" on every case. Then
   `head -40 cases/late_and_timezone/runs/standard/prompt.md` and `grep -A 14 'order by hash' cases/late_and_timezone/runs/standard/prompt.md | head -40`:
   what a model sees, the same queries on every case, and nothing else.
4. **The chart (2 min).** `reports/success_by_type.svg`: found and fixed by type and model, standard sample above,
   curated below. The timezone column and the mixed column carry the story.
5. **Three cases that carry the findings (10 min).**
   - `cases/duplicates_and_rename/runs/opus-3.md` against `runs/standard/opus-3.md`: the copied payment seen and handed
     back ("only if a legitimate repeat payment of the same amount can't happen"), then not seen at all. Nine runs,
     nine misses, both ways; `score.md`, "What it says".
   - `cases/rename_and_utc/runs/sonnet-1.md` against `runs/standard/sonnet-1.md`: 8 of 9 fixed with order 73's
     timestamp in the curated sample, 0 of 9 without it; both Sonnet runs that commit to UTC guess Sydney. The cost of a sample that does
     not point.
   - `cases/rename_and_new_status/runs/standard/prompt.md`, the `raw_orders` rows: two `delivered` rows the hash
     happened to pick, and the case goes from 3 of 9 to 6 of 9. The luck of a sample that does not point.
6. **Numbers and caveats (2 min).** `reports/pilot-summary.md`.
7. **Questions (2 min).** Logged in the week note afterwards.
