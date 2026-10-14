claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 16 s | USD 0.085 | 2026-10-15 | project ad0b16b | prompt c1e2f11386cd

## 1. Root cause

**`models/staging/stg_orders.sql`** reads only `status` from `raw_orders`, but the raw table now has two status columns: `status` and `order_status`.

The upstream feed appears to have moved from `status` to `order_status`. Newer rows, mostly March and April, have `status = NULL` and the real value only in `order_status`. The samples show this: order 84 has `status` NULL and `order_status` `placed`, and order 78 has NULL and `shipped`. Older rows have both columns filled.

The effects are:
- `stg_orders.status` is NULL for 43 orders, which are exactly the NULL-status group in `orders` (amount 761.00).
- In `customers.sql`, `case when orders.status = 'completed'` is false for NULL, so those orders drop out of `completed_lifetime_value`.
- Orders that are really completed are no longer counted. That would explain the 1103 → 824 drop (279.00 missing). I can't confirm the 279 without the seed data, so run the query below.
- `customer_lifetime_value` (1672) and `sum(orders.amount)` are unaffected, because neither depends on status.

Every test passed because:
- `accepted_values` ignores NULLs.
- Nothing asserts `not_null` on `status`.
- Nothing reconciles the completed total.

## 2. Fix

In `stg_orders.sql`:

```sql
    select
        id as order_id,
        user_id as customer_id,
        ordered_at,
        cast(ordered_at as date) as order_date,
        coalesce(status, order_status) as status

    from source
```

To confirm the diagnosis before rebuilding:

```sql
select order_status, count(*), sum(p.amount)/100
from raw_orders o left join raw_payments p on p.order_id = o.id
where o.status is null group by 1;
```

The `completed` row should come to about 279. Then run `dbt build` and check that `sum(completed_lifetime_value)` is 1103.00.

Also check whether the two columns ever disagree on rows where both are set. If so, decide which is authoritative rather than blindly using `coalesce`.

## 3. Tests that would have caught it

In `models/staging/schema.yml`, add `not_null` to `stg_orders.status`:

```yaml
      - name: status
        tests:
          - not_null
          - accepted_values:
              arguments:
                values: ['placed', 'shipped', 'completed', 'return_pending', 'returned']
```

The same `not_null` belongs on `orders.status`. The `accepted_values` test alone is blind to NULLs.

Add a reconciliation singular test, `tests/assert_completed_ltv_matches_orders.sql`:

```sql
select o.completed, c.completed
from (select sum(amount) as completed from {{ ref('orders') }} where status = 'completed') o
cross join (select sum(completed_lifetime_value) as completed from {{ ref('customers') }}) c
where abs(o.completed - c.completed) > 0.01
```

Add a second singular test, `tests/assert_status_columns_not_both_populated_differently.sql`, for upstream drift in the raw feed:

```sql
select * from {{ ref('raw_orders') }}
where status is not null and order_status is not null and status <> order_status
```

For a longer-term guard, add a `source freshness`/schema-drift check or a `not_null` test on whichever raw column is authoritative. The first test above would have failed the build the week the feed changed.
