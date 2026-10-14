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

## Standard sample

**Runs:** three per model, week 8, on the standard output sample (the same queries on every case: row counts, the raw
column list, monthly totals of the output tables and ten rows of every table picked by hash); `runs/standard/prompt.md`
is the frozen prompt every run answered and `runs/standard/` holds the responses. `claude -p --setting-sources ""
--model <model> --tools "" --output-format json` (Claude Code 2.1.299), project ad0b16b. Same rubric; Defects is how
many of the case's injected defects the run named as a cause.

| Run | Root cause | Fix | Defects | Notes |
|---|---|---|---|---|
| sonnet-1 | yes | yes | 1 | This week's rows capitalised (`Completed`, `Placed`, `Shipped`, `Return_pending`), case-sensitive compare in `customers.sql`, 604 + 499 = 1103, both `accepted_values` lists widened; `lower(trim(status))` in `stg_orders` and both lists back to the five lower-case values; notes `return_pending` collapsing from two buckets to one; reconciliation on `lower(status)`. |
| sonnet-2 | yes | yes | 1 | Same chain, the widened lists "turned a test that would have caught the drift into one that blesses it"; `lower(trim(status))` in staging, lists restored; reconciliation on `lower(status)`. |
| sonnet-3 | yes | yes | 1 | Same chain, "roughly half the orders carry capitalized statuses"; `lower(trim(status))` in staging, lists restored, strict test suggested on `raw_orders` as well; reconciliation on `lower(status)`. |
| haiku-1 | yes | yes | 1 | Mixed-case `status` in `raw_orders`, case-sensitive compare, both schema files list the capitalised values; `lower(status)` in `stg_orders`, lists narrowed; reconciliation to the raw tables on `lower(o.status)`. |
| haiku-2 | yes | yes | 1 | Same chain, the lists "enshrine the bad data rather than catching it"; `lower(status)` in staging, lists narrowed, seed left as a faithful copy of the source; reconciliation to raw on `lower(o.status)`. |
| haiku-3 | yes | yes | 1 | Same chain, reads the `completed_lifetime_value` description as proof the model is wrong rather than the docs; `lower(status)` in staging, lists narrowed; per-customer reconciliation on `lower(status)`. |
| opus-1 | yes | yes | 1 | Mixed case in `raw_orders` shown from the sample (order 63 `Completed`, order 16 `completed`), case-sensitive compare in DuckDB, both lists widened; `lower(trim(status))` in staging, lists restored under the tests section with a strict copy suggested on the seed; reconciliation on `lower(status)`. |
| opus-2 | yes | yes | 1 | Same chain, dates it from the sample: "the title-case values appearing on newer orders, so the upstream format likely changed partway through the period"; `lower(trim(status))` in staging, lists restored under the tests section; reconciliation on `lower(status)`. |
| opus-3 | yes | yes | 1 | Same chain; `lower(trim(status))` in staging, lists restored in the fix; reconciliation on `lower(status)`, with the rule not to widen the list to make a failing test pass. |

**Verification.** Every fix matches a shape in the Verification paragraph above.

**What it says.** Nine of nine found and fixed, the same as the six curated Sonnet and Haiku runs and the three Opus runs. The standard sample put both spellings side by side in the status breakdown (`Completed` 31 orders 499.00 above `completed` 36 orders 604.00), and every run added them to the ledger's 1103.00, read the ten-value test names in the log and the lists in both schema files as the widening, and normalised in staging; only opus-1 and opus-2 used the `raw_orders` and `stg_orders` sample rows to say the title case sits on newer orders, and no run dated the cut-over to 15 February as the curated opus-1 did. The curated sonnet-3's flaw, a reconciliation test that filtered `orders` case-sensitively and so passed on the broken data, did not recur: all nine reconciliation tests filter on `lower(status)`, and six of nine added the unrelated `total_order_amount` doc drift as a side note.
