# Score: status_title_case

**Runs:** `claude -p --setting-sources "" --model sonnet --tools "" --output-format json` and the same with `--model haiku` (Claude Code 2.1.296),
isolated from this machine's hooks and global instructions, all tools disabled, one turn each, prompt = `prompt.md`
(seeds hidden). Sonnet is `claude-sonnet-5-5` (16 s, USD 0.05), response in `runs/sonnet-1.md`. Haiku is `claude-haiku-5-5`
(19 s, USD 0.004), response in `runs/haiku-1.md`.

## Claude Sonnet

**Root cause: found.** Both casings in `raw_orders.status`, the case-sensitive comparison in `customers.sql`,
604 + 499 = 1103, and both `accepted_values` lists "edited to accept the bad data". It also named `Return_pending`.
One garbled sentence ("535 orders ... more precisely 67"), corrected in the same line.

**Fix: made the output correct.** `lower(trim(status))` in `stg_orders` and both lists narrowed to the five lower-case
values. Applied: every customer's `completed_lifetime_value` matches the clean data and all 30 tests pass. Its
reconciliation test on `lower(status)` fails with 1 row on the broken data even with the widened lists, passes after the
fix and on clean data (41/41).

**Verdict:** pass on both root cause and fix.

## Claude Haiku

**Root cause: found.** The same chain, naming both schema files and the second affected value.

**Fix: made the output correct.** The same normalisation and narrowing: every customer matches the clean data, 30/30
tests. Its reconciliation test fails with 1 row on the broken data, passes after the fix and on clean data (41/41).

**Verdict:** pass on both root cause and fix.

**Earlier runs (default config, confounded by the ponytail hook, 17 to 22 Oct):** Sonnet pass and pass; Haiku not run.

## Repeat runs

Two more runs per model, same command, prompt assembled by `check_case.py` from the week 3 working tree.

| Run | Root cause | Fix | Notes |
|---|---|---|---|
| sonnet-2 | yes | yes | `lower(trim(status))`, narrowed lists, reconciliation on `lower(status)`. |
| sonnet-3 | yes | yes | Same fix; its reconciliation test filters `orders` with the case-sensitive comparison, so it passes on the broken data (checked) and its claim that it would have fired is wrong. |
| haiku-2 | yes | yes | `lower(status)`, narrowed lists, reconciliation on `lower(status)`. |
| haiku-3 | yes | yes | Same. |

## Opus runs (week 6)

Three runs of `claude-opus-5-5`, same command, prompt assembled by `check_case.py` from the project at f769fdf, before
the week 6 lookup extension, so the same project the other two models saw; verdicts in `results.csv`.

| Run | Root cause | Fix | Notes |
|---|---|---|---|
| opus-1 | yes | yes | Title-case statuses from February, 36 completed plus 31 Completed = 1103, both lists widened; `lower(trim(status))` in staging, lists restored, a casing-proof reconciliation. |
| opus-2 | yes | yes | Same. |
| opus-3 | yes | yes | Same. |

Opus: 3 of 3 found, 3 of 3 fixed.
