# dbt-silent-failures: pilot summary (28 November 2026)

One page for the paper. Numbers from `results.csv` (288 runs); per-run notes and what was verified against the data in
each case's `score.md`.

## Setup

32 cases on one dbt project: dbt Labs' `jaffle_shop` on DuckDB, grown with order timestamps, a regions feed, a
status-filtered revenue column, an incremental daily table and a payment-method lookup (44 tests). Each case is a script
that breaks the raw data, and sometimes a schema file, so that `dbt build` passes with every test green while an output
table is wrong; `check_case.py` verifies the clean build, the injection, the green build after it and a key number on
both sides. Eight silent-failure types with four or more cases each (duplicate rows, join fan-out, schema drift, null
flood, timezone shift, late data, unit change, category change); 11 cases are mixed, combine two types and count toward
both. Three models (`claude-sonnet-5-5`, `claude-haiku-5-5`, `claude-opus-5-5`) answer each case three times through
`claude -p` with tools off, from the project files, a models-and-tests build log, the raw column list and a sample of the
output tables; the seed data is hidden. Two yes/no verdicts per run: root cause found (the run names the change that
broke the data; a hedge between the right cause and a wrong one, or a defect seen and handed back, is no) and complete
fix (the run's fix, applied to the broken data, restores every affected value). Every fix shape named in a ground truth
was applied to the broken data before scoring.

## Numbers

| | Runs | Found | Fixed |
|---|---|---|---|
| Sonnet | 96 | 89 | 81 |
| Haiku | 96 | 77 | 72 |
| Opus | 96 | 89 | 84 |
| All | 288 | 255 | 237 |

By type (found / fixed / runs): duplicate rows 18 / 18 / 18, join fan-out 36 / 36 / 36, schema drift 18 / 18 / 18,
null flood 25 / 25 / 27, timezone shift 24 / 9 / 27, late data 18 / 18 / 18, unit change 25 / 26 / 27, category change
18 / 18 / 18, mixed 73 / 69 / 99. Chart: `reports/success_by_type.svg`.

## Findings

1. **Single-type failures are solved.** On the eight single-type families the three models sit at or near 100% on both
   counts. The one family below is timezone shift: every model finds the switch and places the cut-over at the first
   order whose date visibly moves, so 9 of 27 are fixed and 0 of 18 on the two `utc_switch` cases.
2. **Where the second defect sits decides a mixed case.** Both defects in the complaint: 9 of 9 (`late_and_new_status`,
   `fanout_and_nulls`). The second only in the sample: a total that must reconcile is acted on (`fanout_and_late`, 9 of 9
   found), a row that might be legitimate is seen and handed back (`duplicates_and_rename`, 0 of 9, "may be a legitimate
   split payment or a duplicate load"). The second behind the first fix: 5 of 9 chase a 1.98 residue
   (`category_and_units`), 3 of 9 a status hidden in a column no model reads (`rename_and_new_status`).
3. **The larger model separates only under ambiguity.** Overall Opus is 89 found and 84 fixed against Sonnet's 89 and 81
   and Haiku's 77 and 72. The whole lead is on the mixed cases (27 / 27 against 28 / 24 and 18 / 18 of 33) and on the
   timezone inferences: on `late_and_timezone`'s first prompt Opus was 3 of 3 where the other two were 0 of 6, and on
   `units_and_rename` 3 of 3 against 1 and 0. Where it loses it is by refusing to infer (`rename_and_new_status`, 0 of 3
   against Sonnet's 2).
4. **One clause in the prompt moves a case more than a model tier.** Told that the old rows had reconciled with the
   admin, `late_and_timezone` went from 3 of 9 to 7 of 9 and `rename_and_utc` from 0 of 6 to 5 of 6 on the two smaller
   models. Both prompt versions and every run are in the repo (`runs/first_prompt/` under each case).
5. **A mixed case can be easier than its part.** `rename_and_utc` is 8 of 9 fixed against 0 of 18 on `utc_switch`: the
   rename marks the cut-over that the timezone shift alone never reveals.
6. **The commonest miss is a hand-back, not a wrong answer.** A correct observation ("payment 114 duplicates payment
   68", "the 1.98 is unexplained") followed by "confirm with finance" or "check against the statement" and no change.
   Under the strict rubric that is a miss, and it is where a tool-enabled run would differ most.

## Caveats

Tools off and seeds hidden, so a run cannot query the data. One small project with whole-dollar prices and 99 orders.
One prompt per case, amended once on two cases, both versions kept. The Opus runs on the first 21 cases were made on the
project before the lookup extension, the same version the other two models saw. Verdicts are one reader's, with per-run
notes for audit. About USD 30 for 288 runs.

## Next

The pilot on 7 December, then a bigger real-world project: sources instead of seeds, a load timestamp in the raw tables,
and tools on as a second run mode so the hand-backs can be measured against a run that can look.
