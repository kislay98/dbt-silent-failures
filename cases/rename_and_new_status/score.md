# Score: rename_and_new_status

**Runs:** three per model, `claude -p --setting-sources "" --model <model> --tools "" --output-format json` (Claude Code 2.1.296),
isolated from this machine's hooks, tools off, prompt assembled by `check_case.py` with the seeds hidden and a models-and-tests
build log (`Log: models only`, a real `dbt build --exclude resource_type:seed`), project f769fdf plus the week 6 lookup
extension (committed with week 6); each run header carries the sha256 of the prompt it answered. Sonnet is `claude-sonnet-5-5`,
Haiku is `claude-haiku-5-5`, Opus is `claude-opus-5-5`; responses in `runs/`.
Scored on the week 4 rubric: root cause found yes/no (the run names the change that broke the data), complete fix yes/no (strict,
every affected value right after the fix).

| Run | Root cause | Fix | Notes |
|---|---|---|---|
| sonnet-1 | yes | yes | Both: the rename from order 57, and `delivered` on orders 66 and 67 read as the new name for completed from its place between the completed and shipped runs; `coalesce` plus the mapping, `not_null` on status. |
| sonnet-2 | no | no | The rename, with the whole gap attributed to it; `delivered` noticed as a value that would fail `accepted_values` once read, and the mapping left to a reconciliation the reader must run; `coalesce` alone gives 967.00 and a red test, which it predicts. |
| sonnet-3 | yes | yes | Both; `coalesce` plus the mapping, with a 279.00 check against the admin before relying on it. |
| haiku-1 | yes | yes | Reads `order_status` (backfilled, so equal to the coalesce) and maps `delivered` to completed, with the 279.00 check. |
| haiku-2 | no | no | `coalesce` only; says the admin's 1103.00 "suggests the delivered orders are not counted today", the opposite of the truth, and leaves the value to finance. |
| haiku-3 | no | no | `coalesce(order_status, status)` only; `delivered` left as an open decision, the test failure it will cause called correct. |
| opus-1 | no | no | The rename, and `delivered` noticed "hidden behind the NULLs"; then reads the admin's 279.00 as evidence that `delivered` does not count because `completed` still exists, the opposite of the truth; `coalesce` plus `delivered` added to the lists, which leaves completed revenue at 967.00. |
| opus-2 | no | no | The rename; `delivered` called "a real question, not noise" with the query that would settle it, and the mapping refused until someone runs it; `coalesce` alone gives 967.00 and a red test. |
| opus-3 | no | no | Same: the decisive March query written out, "don't guess", the decision left to the shop team; `coalesce` alone. |

**Verification.** `coalesce(status, order_status)` with `delivered` mapped to `completed` in staging, or with the
`customers` filter widened to both values, restores `customers` and `orders` to the clean values. `coalesce` alone, or
reading `order_status` alone, leaves completed revenue at 967.00 and makes both `accepted_values` tests fail on 8 rows.
`not_null` on `stg_orders.status` fails with 43 rows on the broken data and passes clean; a reconciliation of completed
revenue to the admin fails with 1 row.

**What it says.** Three of nine found and fixed, the lowest of the week, and the larger model scores zero. The second defect is hidden twice over: `delivered` sits in a column no model reads, and once the rename is fixed it shows up as a test failure rather than a wrong number. Three runs (two Sonnet, one Haiku) read the value as the new name for completed from its place in the status sequence and mapped it; the other six saw it, wrote the query that would settle it, and handed the decision back. Under the strict rubric that caution is a miss, and one Opus run inferred the wrong answer from the admin figure outright.
