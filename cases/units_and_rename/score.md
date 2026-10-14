# Score: units_and_rename

**Runs:** three per model, `claude -p --setting-sources "" --model <model> --tools "" --output-format json` (Claude Code 2.1.296),
isolated from this machine's hooks, tools off, prompt assembled by `check_case.py` with the seeds hidden and a models-and-tests
build log (`Log: models only`, a real `dbt build --exclude resource_type:seed`), project bef6b69; each run header carries the
sha256 of the prompt it answered. Sonnet is `claude-sonnet-5-5`, Haiku is `claude-haiku-5-5`; responses in `runs/`.
Scored on the week 4 rubric: root cause found yes/no (the run names the change that broke the data), complete fix yes/no (strict,
every affected value right after the fix).

| Run | Root cause | Fix | Notes |
|---|---|---|---|
| sonnet-1 | yes | no | Both changes: the rename, and "some rows, probably coupons, in dollars" from the 0.99 pattern in the gaps; `coalesce` plus a rule that takes every NULL-method coupon row as dollars, which turns order 58's 18.00 coupon (1 March, still cents) into 1800.00; it noted that 18.00 might be a cents row and still shipped the rule behind a "confirm first". |
| sonnet-2 | yes | no | Both changes, reconciled to the cent (0.33 in April is 2 + 7 + 24 divided by 100); the same rule, same 1800.00 on order 58, which its own arithmetic had listed as a correct 18.00. |
| sonnet-3 | yes | yes | Both changes; `coalesce` now, and the dollar rows corrected at the source once a query says which rows they are, with no guessed rule. |
| haiku-1 | no | no | The rename only; says the 26.73 and 32.67 gaps "are not explained by this bug" and guesses orphan payments; `coalesce` only. |
| haiku-2 | no | no | The rename only; the gap "probably not explained by the NULL method alone", orphans suggested. |
| haiku-3 | no | no | The rename only; the gap given "a different cause", orphans again. |

**Verification.** `coalesce(payment_method, method)` plus a hundredfold on the six dollar rows (by payment id 86 and
later, by order date from 15 March, or by raw amount under 100) restores `orders` exactly. The rule "NULL-method
coupon rows are dollars" over-applies to payment 66 and makes order 58 1806.00. Either half alone leaves `orders`
wrong: the rename alone keeps `amount` at 1612.60, the coupons alone keep the method columns at 0 from March.
`not_null` on `payment_method` fails with 49 rows, the methods-add-up test with 42, the sub-dollar test with 6; all
pass clean.

**What it says.** Three of six found, one of six fixed, the lowest of the week. The second defect hides behind the
first: once the breakdown is explained by the rename, the leftover gap in the totals gets a new story (orphans) rather
than a second look at the 0.02 rows in the sample. Sonnet found both and then over-generalised the second into a rule
that breaks the one cents coupon after the rename.
