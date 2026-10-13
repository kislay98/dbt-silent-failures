# Score: status_renamed

**Runs:** `claude -p --setting-sources "" --model sonnet --tools "" --output-format json` and the same with `--model haiku` (Claude Code 2.1.296),
isolated from this machine's hooks and global instructions, all tools disabled, one turn each, prompt = `prompt.md`
(seeds hidden). Sonnet is `claude-sonnet-5-5` (15 s, USD 0.05), response in `response.md`. Haiku is `claude-haiku-5-5`
(20 s, USD 0.004), response in `response_haiku.md`.

## Claude Sonnet

**Root cause: found.** Two status columns in `raw_orders`, the 43 March and April orders with `status` NULL,
`stg_orders.sql` selecting only `status`, the case expression in `customers.sql` treating NULL as not completed, 824
against 1103, `accepted_values` ignoring NULL and no `not_null` on status. It asked for the vocabulary of `order_status`
to be confirmed first; on the broken data it carries the same five values.

**Fix: made the output correct.** `coalesce(status, order_status) as status`. Applied with the downstream models re-run:
every customer's `completed_lifetime_value` matches the clean data. Its `not_null` tests on both status columns fail
with 43 rows each on the broken data and pass after the fix. Its singular test (both columns NULL) returns 0 rows on the
broken data: a guard for the next rename, not a detector for this one, which it said.

**Verdict:** pass on both root cause and fix.

## Claude Haiku

**Root cause: found.** The same chain, and it said it could not confirm from the sample that exactly 279.00 of the NULL
group was completed. Its confirmation query shows it: 17 completed orders in the NULL group, 279.00.

**Fix: made the output correct.** The same `coalesce`. Applied: every customer matches the clean data. Its `not_null`
tests fail with 43 rows each on the broken data and pass after the fix.

**Verdict:** pass on both root cause and fix.

**Earlier runs (default config, confounded by the ponytail hook, 17 to 22 Oct):** Sonnet pass and pass; Haiku not run.
