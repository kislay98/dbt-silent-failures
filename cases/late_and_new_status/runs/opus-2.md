claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 54 s | USD 0.187 | 2026-10-14 | project f769fdf+wk6ext | prompt a91015185e01

# Diagnosis: two defects, both caused by the 'delivered' orders

The numbers point to one change in the raw data that broke two models in different ways.

**The completed-revenue gap is exactly the `delivered` orders:**

| | `orders` completed | `orders` delivered | Sum | Admin |
|---|---|---|---|---|
| March | 143.00 | 136.00 | **279.00** | 279.00 |
| Since Jan | 424 + 400 + 143 = 967.00 | 65 + 136 = 201.00 | **1168.00** | 1168.00 |

**The February dashboard gap is exactly orders 100–105:** 6 orders worth 12 + 9 + 15 + 6 + 20 + 3 = 65.00. That accounts for 480 − 415 = 65 and 33 − 27 = 6. They are the same six dates as in your diff query (02-03, 02-08, 02-13, 02-17, 02-22, 02-26). These orders are real, not duplicates. For example, customer 15's only order is 101, and `orders` matches the admin's February total.

## 1. Root cause

**What changed in the data (`raw_orders`):** The shop started sending a new status, `delivered`, in place of `completed`. January has none, February has them only on the backfilled orders, and March has 8. The admin counts `delivered` as completed revenue. On top of that, orders 100–105 were loaded late. Their IDs come after the April orders (98, 99), but their `ordered_at` dates are in February.

Someone added `'delivered'` to `accepted_values` in both `models/schema.yml` and `models/staging/schema.yml`. That is why every test passes. But the business logic was never updated:

- **`models/customers.sql`** (`customer_payments` CTE) uses `case when orders.status = 'completed'`. Delivered orders count toward `customer_lifetime_value` but not toward `completed_lifetime_value`. Customers 2 and 15 show this: their lifetime values are 35.00 and 9.00, but their completed values are 0.00.
- **`models/daily_revenue.sql`** is incremental and only rebuilds `order_date >= max(order_date) - 3 days`. The backfilled February orders arrived when `max(order_date)` was already in April, so they fell outside the window and never got in. The model's assumption is that late data only arrives within 3 days of the newest order, and backdated rows break it. None of the tests compare `daily_revenue` against `orders`, so the gap went unnoticed.

## 2. Fix

**a) Define "completed" in one place.** Add a flag in `models/staging/stg_orders.sql` and keep the raw status unchanged:

```sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,
        ordered_at,
        cast(ordered_at as date) as order_date,
        status,
        status in ('completed', 'delivered') as is_completed

    from source

)
```

Then use the flag in `models/customers.sql`:

```sql
        sum(case when orders.is_completed then amount else 0 end) as completed_amount
```

It's also worth adding `orders.is_completed` to `orders.sql`, so analysts stop writing `status = 'completed'` themselves. The output sample above was built exactly that way, which is how the 143.00 figure appeared. Also update `doc("orders_status")` to explain `delivered`.

**b) Make `daily_revenue` correct regardless of when data arrives.** It aggregates `orders`, which is a table rebuilt in full on every run and only about 100 rows. The incremental setup saves nothing and loses late rows. Change the config to:

```sql
{{ config(materialized='table') }}
```

and remove the `{% if is_incremental() %}` block. If it has to stay incremental, the lookback needs to be based on load time, not `order_date`. That requires the ingestion job to add a `loaded_at` column to `raw_orders`, which doesn't exist today.

**c) Repair the existing data:**

```
dbt build --full-refresh --select stg_orders+
```

Then check that February in `daily_revenue` shows 33 orders and 480.00, and that `sum(completed_lifetime_value)` is 1168.00.

## 3. Tests that would have caught this

**Reconcile `daily_revenue` against `orders`.** This would have failed on the six February dates. Save it as `tests/daily_revenue_matches_orders.sql`:

```sql
with expected as (

    select order_date, count(*) as orders, sum(amount) as revenue
    from {{ ref('orders') }}
    group by order_date

)

select
    coalesce(expected.order_date, daily.order_date) as order_date,
    expected.orders as expected_orders,
    daily.orders as actual_orders,
    expected.revenue as expected_revenue,
    daily.revenue as actual_revenue
from expected
full outer join {{ ref('daily_revenue') }} as daily
    on expected.order_date = daily.order_date
where expected.orders is distinct from daily.orders
   or abs(coalesce(expected.revenue, 0) - coalesce(daily.revenue, 0)) > 0.005
```

**Require every status to be classified.** The real failure was that `accepted_values` let in a new status without anyone deciding whether it counts as revenue. To fix that, link the allowed statuses to the classification itself. Create a seed `seeds/order_statuses.csv` with columns `status,is_completed` (for example `completed,true` and `delivered,true`), and have `stg_orders` take `is_completed` from that seed instead of the hardcoded list. Then add:

```yaml
  - name: stg_orders
    columns:
      - name: status
        tests:
          - not_null
          - relationships:
              arguments:
                to: ref('order_statuses')
                field: status
```

With this in place, a new status like `delivered` fails the build until someone adds a row to the seed and decides whether it counts as completed. That is a decision for finance, and it should not happen by accident.

## Related risks (not causing this issue)

- **`raw_customer_regions` has an `updated_at` column**, which suggests it stores history (several rows per customer). There's no `unique` test on `customer_id`. If a customer has more than one row, the join in `region_revenue.sql` duplicates that customer's revenue.
- **`raw_payment_methods` has a `processor` column**. If a payment method ever has two processors, the left join in `orders.sql` doubles `amount`.
- **Fix for both:** add `unique` tests on `raw_customer_regions.customer_id` and `raw_payment_methods.payment_method`, or deduplicate the seeds in the models before joining.
