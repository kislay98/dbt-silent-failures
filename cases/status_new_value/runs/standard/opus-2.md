claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 26 s | USD 0.176 | 2026-10-15 | project ad0b16b | prompt 1b2ae9e32b52

# Root cause: `delivered` orders aren't counted as completed

**The gap is exactly the `delivered` orders.**

| | Admin | dbt | Gap |
|---|---|---|---|
| Since January | 1103.00 | 967.00 (`completed`) | **136.00** |
| March | 279.00 | 143.00 | **136.00** |

In `orders`, `status = 'delivered'` has 8 orders totalling **136.00**. The gap is the same in March and overall, so all 8 `delivered` orders must be March orders. I worked that out from the totals; I didn't query it directly.

## 1. Root cause

**What's wrong in the data:** starting in March, the shop's source system writes `status = 'delivered'` into `raw_orders` for orders it used to mark `completed`. The admin counts these as completed. The warehouse does not.

**Where it breaks:**
- `jaffle_shop/models/customers.sql`, in the `customer_payments` CTE, uses `case when orders.status = 'completed' ...`. That only matches the literal `'completed'`, so every `delivered` order drops out of `completed_lifetime_value`. Any "completed" filter on `orders.status` has the same problem, which is why March shows 143 instead of 279.
- Total revenue (`daily_revenue`, `orders.amount`, `region_revenue`) doesn't filter on status. That's why it still matches the admin every month.

**Why the tests passed:** `'delivered'` was added to the `accepted_values` lists in `models/staging/schema.yml` (`stg_orders.status`) and `models/schema.yml` (`orders.status`). The one test that would have flagged the new status was changed to accept it. Nothing ever mapped it to its business meaning.

## 2. Fix

Normalize the status once, in staging, so every downstream model uses the same definition. In `jaffle_shop/models/staging/stg_orders.sql`:

```sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,
        ordered_at,
        cast(ordered_at as date) as order_date,
        -- the shop started emitting 'delivered' for completed orders in March 2018
        case when status = 'delivered' then 'completed' else status end as status,
        status as source_status

    from source

)
```

Then remove `'delivered'` from both `accepted_values` lists, so `stg_orders.status` and `orders.status` again allow only `['placed', 'shipped', 'completed', 'return_pending', 'returned']`.

After a rebuild, `sum(completed_lifetime_value)` should be **1103.00**, and March completed orders should be **279.00**.

Before shipping, confirm with the shop or ingestion owners that `delivered` really means `completed` and isn't a separate step before it. Finance's admin figures suggest they're the same. If they turn out to be different, keep both values and change the filter in `customers.sql` to `orders.status in ('completed', 'delivered')`.

## 3. A test that would have caught it

Put the canonical status check on the raw input, where it can't be relaxed to make a downstream change pass. Pair it with a reconciliation test that checks the models against each other.

```yaml
# models/staging/schema.yml — stg_orders
      - name: source_status
        tests:
          - accepted_values:
              arguments:
                values: ['placed', 'shipped', 'completed', 'return_pending', 'returned']
```

This would have failed the night `delivered` first showed up. That forces a decision about the new status instead of silently dropping its revenue.

```sql
-- tests/assert_completed_revenue_reconciles.sql
-- completed revenue per customer must equal completed revenue per order
with c as (select coalesce(sum(completed_lifetime_value), 0) as v from {{ ref('customers') }}),
     o as (select coalesce(sum(amount), 0) as v from {{ ref('orders') }} where status = 'completed')
select * from c, o where abs(c.v - o.v) > 0.01
```

The reconciliation test guards against the two models drifting apart. On its own it wouldn't have caught this bug, because both models dropped `delivered` the same way. The `accepted_values` test on the raw status is the one that catches it.

Also, `models/schema.yml` documents a `total_order_amount` column on `customers`, but the model outputs `customer_lifetime_value`. Worth fixing while you're editing that file.
