claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 28 s | USD 0.005 | 2026-10-14 | project bef6b69

## 1. Root cause

The gap is exactly the March `delivered` orders. Admin March completed = 279.00 = 143.00 (`completed`) + 136.00 (`delivered`). Admin since January = 1103.00 = 967.00 (`completed`, matching `customers.completed_lifetime_value`) + 136.00. Jan and Feb have no `delivered` rows, so March is the only month affected.

The project never decides what `delivered` means for revenue. Three places compare status literally:

- **`jaffle_shop/models/customers.sql`**: `sum(case when orders.status = 'completed' then amount else 0 end)` excludes `delivered`.
- **`jaffle_shop/models/staging/stg_orders.sql`**: passes `status` through unchanged, so the raw `delivered` value reaches every downstream model.
- **`jaffle_shop/models/schema.yml`** and **`jaffle_shop/models/staging/schema.yml`**: the `accepted_values` lists include `'delivered'`. That makes the test pass for any `delivered` row, so the test confirms the value is legal but never checks whether it is revenue. Those tests are green for the wrong reason.

The data itself is probably the source of the change. `delivered` appears only in March, which suggests the ingestion job started emitting it for orders that were previously `completed`. Nothing in the warehouse tells us which meaning is correct. This analysis assumes the admin is right, since finance's figures come from it and it matches the total revenue. If `delivered` is actually an in-transit state that shouldn't count yet, the admin is the one that's wrong and the fix goes the other way.

## 2. Fix

Normalize the status once, in staging, so every downstream model sees the same vocabulary. Edit `jaffle_shop/models/staging/stg_orders.sql`:

```sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,
        ordered_at,
        cast(ordered_at as date) as order_date,
        -- the ingestion job emits 'delivered' for orders the business counts as completed
        case when status = 'delivered' then 'completed' else status end as status

    from source

)
```

Then remove `'delivered'` from the `accepted_values` lists in `jaffle_shop/models/staging/schema.yml` (`stg_orders.status`) and `jaffle_shop/models/schema.yml` (`orders.status`), so the tests check the normalized set:

```yaml
values: ['placed', 'shipped', 'completed', 'return_pending', 'returned']
```

No change is needed in `customers.sql`. After the next `dbt build`, `orders` and `customers` will show March completed = 279.00 and total completed = 1103.00, matching the admin. `orders` is a table, so it rebuilds in full and there is no backfill. `daily_revenue` doesn't filter on status and is unaffected.

## 3. Test that would have caught it

A reconciliation test against the admin's monthly figures. Add a seed `jaffle_shop/seeds/admin_monthly_completed_revenue.csv` with columns `month,completed_amount` (finance can supply the values), then add `jaffle_shop/tests/assert_completed_revenue_matches_admin.sql`:

```sql
with dbt_monthly as (

    select
        date_trunc('month', order_date) as month,
        sum(amount) as completed_amount

    from {{ ref('orders') }}
    where status = 'completed'
    group by 1

)

select
    admin.month,
    admin.completed_amount as admin_amount,
    dbt_monthly.completed_amount as dbt_amount

from {{ ref('admin_monthly_completed_revenue') }} as admin
left join dbt_monthly
    on admin.month = dbt_monthly.month

where abs(coalesce(dbt_monthly.completed_amount, 0) - admin.completed_amount) > 0.01
```

Before the fix, this returns March (admin 279.00, dbt 143.00). The `accepted_values` tests could not catch this, because they only check that a status is one of the allowed values.

## Side finding (not part of this bug)

`raw_customer_regions` has an `updated_at` column, which suggests the feed holds history, but the seed has only a `not_null` test on `customer_id`. If a customer has more than one row, `region_revenue` joins them and multiplies that customer's revenue. Check that the seed has one row per customer, and add a `unique` test on `customer_id` in `jaffle_shop/seeds/` or the schema file. I haven't verified this from the data, since the seed contents weren't provided.
