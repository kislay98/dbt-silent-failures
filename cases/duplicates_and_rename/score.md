# Score: duplicates_and_rename

**Runs:** three per model, `claude -p --setting-sources "" --model <model> --tools "" --output-format json` (Claude Code 2.1.296),
isolated from this machine's hooks, tools off, prompt assembled by `check_case.py` with the seeds hidden and a models-and-tests
build log (`Log: models only`, a real `dbt build --exclude resource_type:seed`), project 8ee5734 (week 6 committed, with the
lookup extension); each run header carries the sha256 of the prompt it answered. Sonnet is `claude-sonnet-5-5`, Haiku is
`claude-haiku-5-5`, Opus is `claude-opus-5-5`; responses in `runs/`.
Scored on the week 4 rubric: root cause found yes/no (the run names the change that broke the data), complete fix yes/no (strict,
every affected value right after the fix).

| Run | Root cause | Fix | Notes |
|---|---|---|---|
| sonnet-1 | no | no | The rename from payment 65 and the NULL method zeroing the pivot and the fees; `coalesce(payment_method, method)`, full refresh. Payments 68 and 114 "may be a legitimate split payment or a duplicate load", left to the processor statement. |
| sonnet-2 | no | no | Same; "if it's off by exactly 28.00 after the rebuild, look at payments 68 and 114", not decided. |
| sonnet-3 | no | no | Same; ids 114 to 118 "beyond the original range", "may be legitimate or a double load", to be compared with the statement; `--full-refresh` only "if you remove a duplicate". |
| haiku-1 | no | no | The rename only; payment 114 listed among the NULL rows without comment. |
| haiku-2 | no | no | The rename, and payment 114 "probably a duplicated payment row" with the downstream effect spelled out, then "if confirmed" remove it; the rule for finding the copies left open. |
| haiku-3 | no | no | The rename; the pair "may be a genuine second payment or a duplicate", to check after the rebuild against the 334.00. |
| opus-1 | no | no | The rename, with the `accepted_values` NULL semantics spelled out and the fees effect; the copies "unconfirmed": "if March card volume comes out above 334, look at payments with IDs above 113 first". |
| opus-2 | no | no | Same; "if the processor statement shows only one, it's a duplicate that inflates March", a separate issue to reconcile. |
| opus-3 | no | no | Same; ids 114 to 118 "look like re-delivered rows" with a query to confirm and a `qualify row_number()` dedupe as a stopgap, "only if a legitimate repeat payment of the same amount can't happen"; not applied. |

**Verification.** `coalesce(payment_method, method)` (or `method` alone) in `stg_payments` with the five copies removed, by id or as one row per order, method and amount, restores `orders`, `customers` and `region_revenue` exactly; no order in the clean data has two payments of the same method and amount, so the dedupe is safe. The rename fix alone leaves `sum(orders.amount)` at 1765.00 with the copies now counted in their method columns; dropping the copies alone leaves every method column at 0 from March. `not_null` on `stg_payments.payment_method` fails with 54 rows on the broken data, a uniqueness check on order, method and amount with 5, and both pass clean.

**What it says.** Nine of nine named the rename and none is scored found: every run saw payment 114 next to payment 68 in the sample and every run handed it back ("may be a legitimate split payment or a duplicate load", "if March card volume comes out above 334", "only if a legitimate repeat payment of the same amount can't happen"). The copy is in the sample but not in the complaint, and with tools off no run would act on it; the larger model went furthest and stopped at a conditional stopgap. The same shape as `rename_and_new_status`: the second defect is seen and not decided, so under the rubric it is a miss nine times.
