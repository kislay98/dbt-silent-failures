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

## Standard sample

**Runs:** three per model, week 8, on the standard output sample (the same queries on every case: row counts, the raw
column list, monthly totals of the output tables and ten rows of every table picked by hash); `runs/standard/prompt.md`
is the frozen prompt every run answered and `runs/standard/` holds the responses. `claude -p --setting-sources ""
--model <model> --tools "" --output-format json` (Claude Code 2.1.299), project ad0b16b. Same rubric; Defects is how
many of the case's injected defects the run named as a cause.

| Run | Root cause | Fix | Defects | Notes |
|---|---|---|---|---|
| sonnet-1 | yes | yes | 2 | Both: the feed moved to `order_status` from March, leaving `status` NULL on 43 orders (761.00), and `delivered` in the new column read as the new name for completed, "I inferred it from the numbers"; `coalesce(status, order_status)` with `delivered` mapped to `completed` in `stg_orders`, a 279.00 check on the raw tables before shipping, then `dbt build`; `not_null` on status and a reconciliation of `customers` to the raw columns. |
| sonnet-2 | yes | yes | 2 | Both; same `coalesce` plus mapping, the mapping called "an assumption" to confirm with the 279.00 query before merging; `not_null` on status and a singular test that the two raw status columns agree. |
| sonnet-3 | yes | yes | 2 | Both; same `coalesce` plus mapping, inferred "from the numbers (279 of March was completed)", with a question to the ingestion owners whether `delivered` is a new state; `not_null` on status and a completed-revenue reconciliation to `orders`. |
| haiku-1 | no | no | 1 | The rename only; `delivered` noticed as a value the test never sees, but the 279.00 placed on rows with `order_status = 'completed'` and the mapping refused: "I wouldn't guess what `delivered` maps to. It may be `shipped`"; `coalesce(status, order_status)` alone, 967.00 and a red `accepted_values`. |
| haiku-2 | no | no | 1 | The rename; `delivered` a secondary issue whose meaning "the business owner has to confirm", the mapping written into the code "ONLY if finance confirms that is the meaning" and "If the `delivered` mapping is not approved, leave it out", so offered conditionally and `coalesce` alone is what stands, 967.00 and a red test. |
| haiku-3 | no | no | 1 | The rename; reads the admin figure as meaning "`'delivered'` is not counted as completed" and recommends `coalesce(status, order_status)` alone, the mapping left for finance to choose, the test failure it causes called the test working as intended. |
| opus-1 | yes | yes | 2 | Both: the rename, and `order_status` "reports completed orders as `delivered`"; `coalesce(order_status, status)` with `delivered` mapped to `completed`, the same result because the new column is backfilled, then a rebuild; the check expects all 279.00 under `delivered` when 143.00 of it is `completed` in the new column; `not_null` on status. |
| opus-2 | yes | yes | 2 | Both: "Orders the shop admin counts as completed arrive as `delivered`, not `completed`"; `coalesce` of the mapped `order_status` over `status`, then `dbt build`; the check likewise expects `delivered` alone to make 279.00; `not_null` on status and `accepted_values` on the raw `order_status`. |
| opus-3 | yes | yes | 2 | Both: the new column "uses a different word for completed orders: `delivered`"; `coalesce(order_status, status)` with the mapping, then `dbt build`; `not_null` on status and a singular test that the two raw columns agree after the mapping. |

**Verification.** Every fix matches a shape in the Verification paragraph above.

**What it says.** Six of nine found and fixed against three of nine curated, with Sonnet and Opus three for three and Haiku zero for three. The standard sample's ten `raw_orders` rows happened to include orders 73 and 67 with `status` NULL and `order_status = 'delivered'` beside older rows with `completed` in both columns, and the `orders` by status table showed 43 NULL rows (761.00) equal to March plus April; every run used both together with 1103 - 824 = 279, and the six that scored read `delivered` as the new name for completed from the 279.00 admin figure and wrote the mapping into the fix with a check attached. The three Haiku runs saw the same rows and handed the mapping back: "I wouldn't guess what `delivered` maps to" (haiku-1), "ONLY if finance confirms" (haiku-2), "`'delivered'` is not counted as completed" (haiku-3). The Opus checks expect all 279.00 under `delivered` when 143.00 of it is `completed` in the new column, so a reader running them would see the total split in two, not a miss.
