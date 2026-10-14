claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 30 s | USD 0.005 | 2026-10-14 | project 7d5b525+dirty

## 1. Root cause

There are two separate bugs. Together they explain all three symptoms.

**A. Duplicate payment rows in the raw payments seed (`seeds/raw_payments.csv`), which flow into `orders.amount`.**

- `raw_payments` has 131 rows for 105 orders. `stg_payments` passes its `unique` test on `payment_id`, so the duplicates must carry distinct IDs. That means the backfill re-issued payments under new IDs rather than repeating existing rows.
- `stg_payments.sql` converts cents to dollars and does no deduplication.
- `orders.sql` sums every payment row per order (`sum(amount) as total_amount`), so each duplicate is added to `orders.amount`.
- Order 1 has a second payment row of 10.00, which turns 10.00 into 20.00. The 180.00 gap between the warehouse (1917.00) and the settlement (1737.00) is the total of the duplicate rows.

I can't see the seed rows, so this is inferred from the numbers. Confirm it with:

```sql
-- candidate duplicates
select order_id, payment_method, amount, count(*) as n, sum(amount) as dup_total
from stg_payments
group by all
having count(*) > 1;

-- excess amount; should equal 180.00 if this is the whole gap
select sum(amount) - sum(distinct_amount) as excess
from (
  select amount, row_number() over (partition by order_id, payment_method, amount order by payment_id) as rn,
         sum(amount) over () as _
  from stg_payments
) ...
```

A simpler check: `select sum(amount) from stg_payments where payment_id not in (<ids of the duplicate rows, keeping the first of each group>)` should return 1737.00. Confirm the dedup key against the processor's transaction references before relying on `(order_id, payment_method, amount)`. Two genuine payments with the same method and amount on one order would be wrongly merged by that key.

**B. `daily_revenue` never rebuilds the backfilled history (`models/daily_revenue.sql`).**

- It is `incremental` with `delete+insert`, and on each run it only rebuilds `order_date >= max(order_date) - 3 days`.
- The February backfill and the older January rows are outside that window, so they are never recomputed. Feb rows are missing (6 orders absent: 99 vs 105), and several January/February revenue values are stale (for example 2018-01-01 shows 10.00 against 20.00 in `orders`).
- The 1686.00 total is therefore stale history plus missing days, not a sum of current `orders`.

**Why the tests pass:** none of them compare totals. `unique`, `not_null`, and `accepted_values` are all satisfied by duplicate payments with new IDs. Nothing compares `daily_revenue` to `orders`, and nothing compares `orders` to the settlement.

**Secondary issue, not in this symptom:** `region_revenue.sql` joins `raw_customer_regions` without deduplicating. That seed has an `updated_at` column, so if a customer has more than one region row, `region_revenue` fans out and double-counts revenue. Worth checking before anyone uses that model for reconciliation.

## 2. Fix

**Staging dedupe (after confirming the key with the processor data).** In `models/staging/stg_payments.sql`, drop the re-issued rows:

```sql
renamed as (

    select
        id as payment_id,
        order_id,
        payment_method,
        amount / 100 as amount
    from source
    -- backfill re-issued some payments under new ids; keep one row per payment
    qualify row_number() over (
        partition by order_id, payment_method, amount
        order by payment_id
    ) = 1

)
```

The better long-term fix is upstream: make the backfill stop re-issuing IDs, or carry the processor's transaction reference into the seed and dedupe on that. This is a judgement call, so I'd confirm it with whoever owns the load.

**Daily revenue.** Either:
- Run `dbt build -s daily_revenue --full-refresh` once to rebuild all history, and
- change `daily_revenue` to `materialized='table'`. The table is small and the incremental window can't handle backfills.

**Region revenue.** Dedupe the regions feed to one row per customer, for example with `qualify row_number() over (partition by customer_id order by updated_at desc) = 1` in a staging model, and join that.

Then run `dbt build --full-refresh` for the affected models and recheck `sum(orders.amount)` against 1737.00 once the duplicate rows are removed.

**Docs drift:** `schema.yml` documents `total_order_amount` on `customers`, which the model doesn't produce. Fix that while you're in there.

## 3. Tests that would have caught this

**Reconciliation test: `daily_revenue` must match `orders` for every date.** Singular test at `tests/assert_daily_revenue_matches_orders.sql`:

```sql
select
    coalesce(o.order_date, d.order_date) as order_date,
    o.orders, d.orders as daily_orders,
    o.revenue, d.revenue as daily_revenue
from (
    select order_date, count(*) as orders, sum(amount) as revenue
    from {{ ref('orders') }} group by 1
) o
full outer join {{ ref('daily_revenue') }} d using (order_date)
where o.orders is distinct from d.orders
   or abs(coalesce(o.revenue, 0) - coalesce(d.revenue, 0)) > 0.005
```

This fails on the current data (16 dates).

**Duplicate payment test.** Singular test at `tests/assert_no_duplicate_payments.sql`:

```sql
select order_id, payment_method, amount, count(*) as n
from {{ ref('stg_payments') }}
group by 1, 2, 3
having count(*) > 1
```

This catches the re-issued rows. Use `dbt_utils.unique_combination_of_columns` if you prefer, or switch to the processor's transaction reference once it is in the seed.

**Settlement reconciliation.** Add a seed or source with the settlement totals per period and a test that `sum(orders.amount)` equals the settled total for that period. This is the only test here that checks against the outside world, and it is the one that would have flagged the 180.00 gap at the first build.
