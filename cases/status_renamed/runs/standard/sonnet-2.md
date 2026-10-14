claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 17 s | USD 0.085 | 2026-10-15 | project ad0b16b | prompt c1e2f11386cd

## 1. Root cause

`models/staging/stg_orders.sql` reads `status` from `raw_orders`, but the raw table now has two status columns: `status` and `order_status`. The feed has moved to `order_status`, and `status` is NULL for the newer orders. The raw sample shows this: order 84 has `status = NULL, order_status = 'placed'`, and order 78 has `NULL` / `shipped`.

Because `stg_orders` only selects `status`, 43 orders arrive with a NULL status. `orders.status` shows exactly that: 43 NULL rows totalling 761.00. In `customers.sql`, `case when orders.status = 'completed'` is never true for those rows, so their payments drop out of `completed_lifetime_value`. That gives 824.00 instead of 1103.00.

Total revenue is unaffected, which is why `sum(orders.amount)` is still 1672.00 and `daily_revenue` and `region_revenue` still reconcile. The 279.00 gap (1103 − 824) should be the NULL-status orders whose `order_status` is `completed`. I haven't run a query against the data, so check that figure with the query below.

**Why the tests passed:**
- `accepted_values` ignores NULLs.
- There is no `not_null` test on `status` in either `stg_orders` or `orders`.
- The seed's own tests only cover other columns.

## 2. Fix

In `models/staging/stg_orders.sql`:

```sql
    select
        id as order_id,
        user_id as customer_id,
        ordered_at,
        cast(ordered_at as date) as order_date,
        coalesce(order_status, status) as status

    from source
```

Before merging, check whether the two columns ever disagree. If they do, decide which one is authoritative:

```sql
select id, status, order_status from raw_orders
where status is not null and order_status is not null and status <> order_status
```

To confirm the 279.00 gap:

```sql
select sum(p.amount) from raw_payments p
join raw_orders o on o.id = p.order_id
where o.status is null and o.order_status = 'completed'
```

The payments are in cents, so divide by 100. After the fix, rerun `dbt build` and expect `completed_lifetime_value` to be 1103.00.

## 3. Tests that would have caught this

Add `not_null` on status in `models/staging/schema.yml`, and again in `models/schema.yml` for `orders`:

```yaml
      - name: status
        tests:
          - not_null
          - accepted_values:
              arguments:
                values: ['placed', 'shipped', 'completed', 'return_pending', 'returned']
```

Add a singular test, `tests/assert_completed_ltv_reconciles.sql`. It checks that customer totals tie back to orders, per status, so a silent status change can't hide:

```sql
select 'completed' as check_name
where (select coalesce(sum(completed_lifetime_value), 0) from {{ ref('customers') }})
   <> (select coalesce(sum(amount), 0) from {{ ref('orders') }} where status = 'completed')
```

A source-level guard on the raw feed would also catch schema drift early. It fails whenever the two columns diverge:

```sql
select * from {{ ref('raw_orders') }}
where status is distinct from order_status
```

Set that one to `severity: warn` if the overlap period is expected to be messy.

I also noticed that the `customers` description in `schema.yml` documents `total_order_amount`, but the model outputs `customer_lifetime_value`. That doesn't affect this bug, but it's worth fixing.
