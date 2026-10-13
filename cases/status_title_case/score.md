# Score: status_title_case

**Runs:** `claude -p --setting-sources "" --model sonnet --tools "" --output-format json` and the same with `--model haiku` (Claude Code 2.1.296),
isolated from this machine's hooks and global instructions, all tools disabled, one turn each, prompt = `prompt.md`
(seeds hidden). Sonnet is `claude-sonnet-5-5` (16 s, USD 0.05), response in `response.md`. Haiku is `claude-haiku-5-5`
(19 s, USD 0.004), response in `response_haiku.md`.

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
