claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 25 s | USD 0.006 | 2026-10-15 | project ad0b16b | prompt c1e2f11386cd

## 1. Root cause

**`jaffle_shop/models/staging/stg_orders.sql` reads the wrong status column.** It selects `status` from `raw_orders`, but the raw table also has an `order_status` column that is populated when `status` is not.

In the warehouse, `raw_orders` has both columns. `status` is NULL for 43 of 99 orders, and `order_status` holds the value for those rows. The sample rows agree wherever both are populated (for example id 33 and id 6 are `completed` in both). The `orders` output confirms the gap: 56 orders have a status and 43 are NULL, with a combined `amount` of 761.

`customers.sql` computes `completed_lifetime_value` with `case when orders.status = 'completed'`, so any completed order whose status is NULL in staging contributes 0. That is why completed revenue dropped while `sum(orders.amount)` (1672.00) and `customer_lifetime_value` (1672.00) stayed the same. Those two measures don't depend on status.

I can't reproduce last week's 1103.00 from the data provided. The 279 difference is consistent with completed orders whose `status` went NULL this week while `order_status` still says `completed`, but confirm that with the check below.

**Why the tests passed:** `accepted_values` in dbt only fails on non-NULL values outside the list, so NULL status rows pass. Nothing in `schema.yml` asserts `not_null` on `status` in `stg_orders` or `orders`.

Run this to confirm the mechanism:

```sql
select
    count(*) filter (where status is null and order_status is not null) as null_status_only,
    count(*) filter (where status is not null and order_status is not null and status <> order_status) as conflicting,
    count(*) filter (where order_status = 'completed' and status is distinct from 'completed') as completed_missing
from raw_orders;
```

If `conflicting` is 0 and `completed_missing` is the number of completed orders you expect to be lost, the cause is confirmed.

## 2. Fix

**Code change, `jaffle_shop/models/staging/stg_orders.sql`:**

```sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,
        ordered_at,
        cast(ordered_at as date) as order_date,
        coalesce(order_status, status) as status

    from source

)
```

I'm preferring `order_status` because it is the populated column. Before merging, run the `conflicting` check above. If it returns anything other than 0, the two columns disagree and someone who owns the feed needs to say which one is authoritative.

Once that is in, `customers`, `orders`, and everything downstream rebuild on `dbt build`. Completed revenue should return to the last-week level if the gap is entirely the NULL-status rows.

**Optional data fix:** if other consumers read `raw_orders.status` directly, backfill it in the source (`update raw_orders set status = order_status where status is null`) so the two columns stop diverging.

**Documentation:** `customers` in `models/schema.yml` documents a `total_order_amount` column that the model does not produce. Remove it or add it to `customers.sql`.

## 3. Test that would have caught it

**Schema test**, add to `jaffle_shop/models/staging/schema.yml` under `stg_orders.status`:

```yaml
      - name: status
        tests:
          - not_null
          - accepted_values:
              arguments:
                values: ['placed', 'shipped', 'completed', 'return_pending', 'returned']
```

This fails immediately on the current data (43 NULL rows). Add the same `not_null` under `orders.status`.

**Singular test**, add as `jaffle_shop/tests/assert_stg_orders_status_matches_source.sql`. It checks that staging agrees with the source column the staging model should be using, which catches a silently dropped column even when the NULLs are not visible:

```sql
select
    r.id,
    r.status,
    r.order_status,
    s.status as stg_status
from {{ ref('raw_orders') }} r
join {{ ref('stg_orders') }} s
    on s.order_id = r.id
where s.status is distinct from r.order_status
```

Against the current data this returns the 43 rows where `stg_status` is NULL and `order_status` is set. It passes after the fix.
