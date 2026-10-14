claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 51 s | USD 0.009 | 2026-10-15 | project ad0b16b | prompt 1b2ae9e32b52

## 1. Root cause

The pipeline treats only `status = 'completed'` as completed revenue, but the source also emits `delivered`. Finance's figures equal completed plus delivered:

- All time: 967.00 (completed) + 136.00 (delivered, 8 orders) = **1103.00**, the admin figure.
- March: 143.00 (completed) + 136.00 = **279.00**, the admin figure. This works only if all delivered revenue falls in March, which I haven't confirmed. Run the monthly split on `orders` to check.

The filter lives in `jaffle_shop/models/customers.sql`:

```sql
sum(case when orders.status = 'completed' then amount else 0 end) as completed_amount
```

That line is correct for the status set it knows about. The bug is that `delivered` appears in the data and the model never classifies it.

The tests hid this rather than catching it. `delivered` was added to the `accepted_values` lists in both `jaffle_shop/models/staging/schema.yml` (`stg_orders.status`) and `jaffle_shop/models/schema.yml` (`orders.status`). That made the test accept the new status without anyone deciding whether it counts as completed. The log shows `accepted_values_*_status` passing, and every other test passes because none of them checks revenue classification.

I'm assuming finance is right that delivered orders should count as completed. Confirm that with whoever owns the status vocabulary. If `delivered` is a new upstream status, find out when it first appeared; that date should line up with when the admin and the models diverged.

## 2. Fix

Classify the status once, in staging, and use that flag downstream. In `jaffle_shop/models/staging/stg_orders.sql`:

```sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,
        ordered_at,
        cast(ordered_at as date) as order_date,
        status,

        -- `delivered` is treated as completed, matching the admin reporting
        status in ('completed', 'delivered') as is_completed

    from source

)
```

In `jaffle_shop/models/customers.sql`:

```sql
sum(case when orders.is_completed then amount else 0 end) as completed_amount
```

Also update the description of `completed_lifetime_value` in `jaffle_shop/models/schema.yml` from "status completed" to "status completed or delivered". Add a `not_null` test on `is_completed`.

Expected result after the fix, if the assumption holds: `completed_lifetime_value` sums to 1103.00, and March completed revenue is 279.00.

## 3. A test that would have caught it

Add a reconciliation test that compares monthly completed revenue with the figures finance reports from the admin. This is the check that fails on the current data (March: 143 vs 279). It needs the admin export loaded as a seed, `seeds/raw_admin_completed_revenue.csv`, with columns `month` (date) and `completed_revenue`.

`tests/assert_completed_revenue_matches_admin.sql`:

```sql
with ours as (

    select
        cast(date_trunc('month', order_date) as date) as month,
        sum(amount) as completed_revenue

    from {{ ref('orders') }}
    where is_completed
    group by 1

),

admin as (

    select month, completed_revenue
    from {{ ref('raw_admin_completed_revenue') }}

)

select
    coalesce(ours.month, admin.month) as month,
    coalesce(ours.completed_revenue, 0) as our_revenue,
    coalesce(admin.completed_revenue, 0) as admin_revenue

from ours
full outer join admin on ours.month = admin.month

where abs(coalesce(ours.completed_revenue, 0) - coalesce(admin.completed_revenue, 0)) > 0.005
```

A singular test returns rows on failure, so this fails the build whenever a month disagrees with the admin.

A complementary check: in `schema.yml`, add a `relationships` test from `orders.status` to a small status-mapping seed (`status`, `is_completed`). Then any new status fails the build until someone classifies it explicitly, instead of being quietly accepted.

## Other risks found (not the cause)

- **Seed tests did not run.** The log shows 32 data tests defined but only 28 executed, and no seed nodes appear in the log. The four tests on `raw_customer_regions` and `raw_payment_methods` were skipped. Make sure the nightly job builds or tests those seeds.
- **`region_revenue` can double-count.** `raw_customer_regions` has an `updated_at` column, which suggests it holds history. The model joins on `customer_id` without deduplicating, so a customer with two rows would have their revenue counted twice. Today there are 100 rows for 100 customers, so there is no current impact. Add a `unique` test on `customer_id`.
- **Documentation drift.** `customers` documents a `total_order_amount` column that does not exist; the column is `customer_lifetime_value`.

I have not run any of this. The analysis is based on the pasted build log and query output, and the fixes are untested against the warehouse.
