# Score: regions_fanout_history

**Runs:** three per model, `claude -p --setting-sources "" --model <model> --tools "" --output-format json` (Claude Code 2.1.296),
isolated from this machine's hooks, tools off, prompt assembled by `check_case.py` with the seeds hidden and a models-and-tests
build log (`Log: models only`, a real `dbt build --exclude resource_type:seed`), project bef6b69; each run header carries the
sha256 of the prompt it answered. Sonnet is `claude-sonnet-5-5`, Haiku is `claude-haiku-5-5`; responses in `runs/`.
Scored on the week 4 rubric: root cause found yes/no (the run names the change that broke the data), complete fix yes/no (strict,
every affected value right after the fix).

| Run | Root cause | Fix | Notes |
|---|---|---|---|
| sonnet-1 | yes | yes | History feed, one row per region change, joined on `customer_id` alone; latest row per customer in a `stg_customer_regions` model (`updated_at desc nulls last, region`), left join with an UNASSIGNED bucket; noticed from the log header that the two seed tests did not run. |
| sonnet-2 | yes | yes | Same; `qualify row_number()` on `updated_at desc, region`; left join with UNKNOWN. |
| sonnet-3 | yes | yes | Same; ties on `updated_at` raised with the feed owner. |
| haiku-1 | yes | yes | Same; staging model with a tie-breaker caveat; `updated_at` added to the seed docs. |
| haiku-2 | yes | yes | Same; a `customer_current_region` model with `unique` and `not_null`, and a check that no ordering customer lacks a region row. |
| haiku-3 | yes | yes | Same; reconciles the 68 customers and 119 orders to the six moved customers. |

**Verification.** The latest-row dedupe reproduces the expected table exactly (62 customers, 99 orders, 1672.00 by
current region). Keeping the oldest row reproduces the clean table instead, which is the wrong report now that the
customers have moved; an as-of join on order date also sums to 1672.00 but puts the moved customers' earlier orders
in their old regions, so a total-only reconciliation would not tell the two apart. The `unique` test on the seed
fails with 6 customers on the broken data, the revenue reconciliation with 1 row; both pass clean.

**What it says.** Six of six, and every run chose the current region because the prompt said finance reports by it.
Without that sentence the as-of join would be a defensible alternative; the ground truth records both.
