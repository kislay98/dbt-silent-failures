# dbt-silent-failures: pilot summary (15 October 2026)

One page for the paper. Numbers from `results.csv`: 288 runs on the standard output sample (the headline) and the
same 288 on the curated per-case queries of weeks 1 to 7 (kept as the comparison). Per-run notes and what was verified
against the data in each case's `score.md`.

## Setup

32 cases on one dbt project: dbt Labs' `jaffle_shop` on DuckDB, grown with order timestamps, a regions feed, a
status-filtered revenue column, an incremental daily table and a payment-method lookup (44 tests). Each case is a script
that breaks the raw data, and sometimes a schema file, so that `dbt build` passes with every test green while an output
table is wrong; `check_case.py` verifies the clean build, the injection, the green build after it, a key number on both
sides, and that the case's frozen prompt still matches what it assembles. Eight silent-failure types with four or more
cases each (duplicate rows, join fan-out, schema drift, null flood, timezone shift, late data, unit change, category
change); 11 cases are mixed, combine two types and count toward both. Three models (`claude-sonnet-5-5`,
`claude-haiku-5-5`, `claude-opus-5-5`) answer each case three times through `claude -p` with tools off, from the
project files, a models-and-tests build log and an output sample; the seed data is hidden. The standard sample is the
same on every case: row counts per table, the raw column list, monthly totals of the output tables and ten rows of every
table picked by hash. The curated sample of weeks 1 to 7 was a handful of queries written per case by someone who knew
where the bug sat. A case's prompt is frozen at its first run; a changed prompt is a new case (two cases have a v2, with
their v1 standard runs kept out of the headline). Two yes/no verdicts per run: root cause found (the run names the
change that broke the data; a hedge between the right cause and a wrong one, or a defect seen and handed back, is no)
and complete fix (the run's fix, applied to the broken data, restores every affected value; rebuild commands are not
scored beyond a full refresh where one is needed). Every fix shape was checked against the broken data before scoring;
a third column counts the defects each run named (0 to 2).

## Numbers

| | Standard, found | Standard, fixed | Curated, found | Curated, fixed |
|---|---|---|---|---|
| Sonnet | 89 | 85 | 89 | 81 |
| Haiku | 73 | 66 | 77 | 72 |
| Opus | 91 | 84 | 89 | 84 |
| All (288) | 253 | 235 | 255 | 237 |

By type on the standard sample (found / fixed / runs): duplicate rows 18 / 18 / 18, join fan-out 36 / 36 / 36, schema
drift 18 / 18 / 18, null flood 27 / 26 / 27, timezone shift 22 / 9 / 27, late data 18 / 18 / 18, unit change
25 / 23 / 27, category change 18 / 18 / 18, mixed 71 / 69 / 99. Chart: `reports/success_by_type.svg`, both samples.

## Findings

1. **Single-type failures are solved.** On the eight single-type families the three models sit at or near 100% on
   both counts on both samples. The one family below is timezone shift: every model finds the switch and places the
   cut-over at the first order whose date visibly moves, so 9 of 27 are fixed and 0 of 18 on the two `utc_switch`
   cases, on either sample.
2. **Pointing at the bug was worth little overall and a lot in places.** The curated queries bought 2 found and 2
   fixed of 288, but 18 of the 96 case-and-model cells moved, 10 down and 8 up. On its first standard run
   `rename_and_utc` fell from 8 of 9 fixed to 0: the curated query had shown the one warehouse timestamp that gives
   the 5h30 offset, the standard sample does not, and the two runs that committed to UTC guessed the wrong zone. With
   that timestamp in the complaint (v2) it is 6 of 9, with Haiku 0 of 3. `rename_and_new_status` doubled, 3 to 6 of
   9, because the ten hashed `raw_orders` rows happened to hold two `delivered` rows. A fixed sample is fair across
   cases and not neutral within one; the paper reports both, the standard numbers as the result and the curated ones
   as the ceiling when someone points.
3. **Where the second defect sits decides a mixed case.** Both defects in the complaint: 9 of 9 (`late_and_new_status`,
   `late_and_duplicate`). The second only in the data: a total that must reconcile is acted on (`fanout_and_late`, 7
   of 9 on the standard sample, 9 on the curated), a row that might be legitimate is handed back or not seen
   (`duplicates_and_rename`, 0 of 9 on both: on the curated sample every run saw the copied payment and asked finance,
   on the standard one no run considered duplicates and all nine read the copies' trace backwards as a stale
   dashboard). The second behind the first fix: 6 of 9 chase the 1.98 residue (`category_and_units`), 5 of 9 derive
   a UTC backfill from the reconciliation (`late_and_timezone_v2`; Haiku 0 of 3 takes the docs' "Date (UTC)" over
   "the two agreed on every day").
4. **The larger model separates only on the mixed cases, and narrowly.** On the standard sample Opus is 91 found and
   84 fixed against Sonnet's 89 and 85; on the 33 mixed runs 29 against 28, with Haiku at 14. Haiku is the model
   that depends on being pointed: 77 to 73 found and 72 to 66 fixed from the curated to the standard sample, and 0 of
   6 on the two v2 timezone cases where the other two models are 5 and 6 of 6.
5. **One clause in the prompt moves a case more than a model tier.** Told that the old rows had reconciled with the
   admin, `late_and_timezone` went from 3 of 9 to 7 of 9 and `rename_and_utc` from 0 of 6 to 5 of 6 on the two smaller
   models (curated sample; both prompt versions and every run are in `runs/first_prompt/`). That is why prompts are
   frozen from week 8 on and the two timezone cases got a v2 folder rather than an edit.
6. **The commonest miss is a hand-back, not a wrong answer.** A correct observation ("payment 114 duplicates payment
   68", "the 1.98 is unexplained", "`delivered` is a real question") followed by "confirm with finance" and no change.
   Under the strict rubric that is a miss, and it is where a tool-enabled run would differ most. The commonest red
   herring on the standard sample is the stale incremental table: most runs on the payment cases read its clean monthly
   totals as history to rebuild rather than as the one healthy reference.

## Caveats

Tools off and seeds hidden, so a run cannot query the data. One small project with whole-dollar prices and 99 orders.
Two cases amended once on the curated prompt, both versions kept; no prompt changed after a standard run, and the two
v2 cases are new folders with their v1 runs kept (`headline=no` in `results.csv`). Verdicts are one reader's, with
per-run notes for audit. A second scorer scored 30 random standard runs blind (`reports/blind_standard/`): 89 of 90
verdicts agree (root cause 30 of 30, fix 29 of 30, defects 30 of 30); the one disagreement is `duplicates_and_units`
standard haiku-3, fixed here on its source-side removal of the copies matched to the processor's references, not fixed
for the second scorer because that removal is deferred. The curated Opus runs on the first 21 cases were made on the
project before the lookup extension, the same version the other two models saw. About USD 30 per sample. The whole
build took two and a half days of clock time, 13 to 15 October 2026.

## Next

A bigger real-world project: sources instead of seeds, a load timestamp in the raw tables, and tools on as a second run
mode so the hand-backs can be measured against a run that can look.
