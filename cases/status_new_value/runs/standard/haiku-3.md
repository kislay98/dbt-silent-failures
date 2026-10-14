claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 28 s | USD 0.006 | 2026-10-15 | project ad0b16b | prompt 1b2ae9e32b52

## 1. Root cause

The warehouse counts only `status = 'completed'`, but the admin also counts orders with status `delivered`. The `delivered` status came in with the raw orders feed, and nothing in the project treats it as completed.

The numbers line up:

- Total `delivered` amount in `orders` is 136.00, and the gap between the admin and the warehouse is exactly 136.00 in both views:
  - Since January: 1103.00 (admin) − 967.00 (`completed`) = 136.00
  - March: 279.00 (admin) − 143.00 (`completed`) = 136.00

This implies all delivered orders fall in March. I inferred that from the arithmetic; the sample tables don't show per-order dates for every delivered order. The query in section 2 confirms it.

The file that let this through is `jaffle_shop/models/staging/schema.yml`, with the same problem in `jaffle_shop/models/orders.yml`'s `models/schema.yml`. Both `accepted_values` tests on `status` were widened to include `'delivered'`, so the test passes on the new value instead of flagging it. The `stg_orders.sql` model passes the status through unchanged, so `delivered` reaches `orders` and `customers`. Those models filter on `'completed'` only, so they drop the delivered revenue.

The reconciliation between `customers.completed_lifetime_value` (967.00) and `orders` (967.00) also passes, because both sides use the same wrong definition. That's why every test is green while finance's number is off.

## 2. Fix

Decide with finance that `delivered` means completed for revenue purposes. If it does, normalise it in staging, once, so every downstream model agrees:

`jaffle_shop/models/staging/stg_orders.sql`:
```sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,
        ordered_at,
        cast(ordered_at as date) as order_date,

        -- 'delivered' is the processor's name for a completed order; the admin counts it as completed
        case when status = 'delivered' then 'completed' else status end as status

    from source

)
```

Then remove `'delivered'` from the `accepted_values` lists in `jaffle_shop/models/staging/schema.yml` and `jaffle_shop/models/schema.yml`, so the list is the five canonical statuses.

If finance says `delivered` is not revenue-complete, the change goes the other way: the admin is the one that's wrong, and the warehouse is correct as-is.

Before changing anything, confirm the March split:
```sql
select date_trunc('month', order_date) as month, status, round(sum(amount), 2) as amount
from orders
where status in ('completed', 'delivered')
group by all
order by all;
```

After the fix, March should show 279.00 and January to date should show 1103.00.

## 3. Test that would have caught it

The test that would have caught it is the strict `accepted_values` on `stg_orders.status` with the original five statuses. The first run after the feed started sending `delivered` would have failed the staging test and stopped the build before any mart was built. Someone would then have had to decide what `delivered` means, instead of quietly adding it to the allowed list.

In `jaffle_shop/models/staging/schema.yml`:
```yaml
  - name: stg_orders
    columns:
      - name: status
        tests:
          - accepted_values:
              arguments:
                values: ['placed', 'shipped', 'completed', 'return_pending', 'returned']
```

Keep that list strict. When a new status appears, the change should go through a deliberate edit to the list, with the mapping shown in the same diff.
