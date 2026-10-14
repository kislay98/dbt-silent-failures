claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 34 s | USD 0.185 | 2026-10-14 | project ad0b16b | prompt b1e95fc444b5

# Diagnosis: NULL payment amounts in `raw_payments`

## 1. Root cause

The bug is in the **data**, not the model SQL. Some rows in **`raw_payments`** (read via `ref('raw_payments')` in `models/staging/stg_payments.sql`) were loaded with `amount = NULL`. The output sample shows two of them:

| payment id | order_id | method      | amount |
|-----------:|---------:|-------------|--------|
| 30         | 25       | coupon      | NULL   |
| 55         | 49       | credit_card | NULL   |

The NULLs pass through `stg_payments` unchanged (`NULL / 100` is NULL). In `models/orders.sql`, `sum(payments.amount)` and the per-method `sum(case …)` skip NULLs, so each affected order is quietly under-counted:

- **Order 25** keeps the 20.00 bank transfer and its other non-NULL payments, but loses the 16.00 coupon payment. That gives 42.00 instead of 58.00.
- Across all affected orders, Jan to Mar lose 65.00 in total: 1533 − 1468. By month, `daily_revenue` minus `orders` is 38 for Jan, 21 for Feb and 6 for Mar.

**Why `daily_revenue` still matches the settlement.** It is incremental and only rebuilds `order_date >= max(order_date) - 3 days`. The Jan to Mar rows were computed on earlier runs, when the amounts were still populated, and were never recomputed. April is inside the rebuild window, and it agrees in both tables (139.0). So the dashboard is right only because it is stale. A `--full-refresh` would break it too.

**Why every test passed:**
- `stg_payments.amount` has no `not_null` test.
- `orders.amount not_null` still passes, because a sum over a mix of NULL and non-NULL values is non-NULL. Every affected order has at least one other payment.
- No test reconciles the models against each other.

`customers.customer_lifetime_value` (1607 = 1468 + 139) and `region_revenue` (also 1607) are understated in the same way.

## 2. Fix

Fix the data at the source. Don't paper over it in SQL: `coalesce(amount, 0)` would hide the problem and the totals would still be wrong.

1. **Find the damaged rows:**
   ```sql
   select * from raw_payments where amount is null;
   ```
2. **Reload those payments with their real amounts.** Re-run the ingestion for `raw_payments`, or correct `seeds/raw_payments.csv` if that is what the ingestion job writes. For example, payment 30 should be `1600`, since amounts are in cents. Use the processor settlement as the reference. Also find out why the loader produced NULLs; a failed cast of a non-integer value is a likely cause.
3. **Rebuild everything, including the incremental model**, so it is recomputed from corrected data rather than stale history:
   ```bash
   dbt build --full-refresh
   ```
   Afterwards, `orders.amount` for Jan to Mar should sum to 1533.00, order 25 should be 58.00, and `daily_revenue` should still match.

## 3. Tests that would have caught it

Add a `not_null` test on the payment amount in `models/staging/schema.yml`:

```yaml
  - name: stg_payments
    columns:
      - name: payment_id
        tests:
          - unique
          - not_null
      - name: amount
        tests:
          - not_null
      - name: payment_method
        tests:
          - accepted_values:
              arguments:
                values: ['credit_card', 'coupon', 'bank_transfer', 'gift_card']
```

Also add a reconciliation test, which catches drift whatever the cause. Create `tests/assert_daily_revenue_matches_orders.sql`:

```sql
-- daily_revenue is incremental, so it can silently diverge from orders
with orders_by_day as (

    select order_date, count(*) as orders, sum(amount) as revenue
    from {{ ref('orders') }}
    group by order_date

)

select
    coalesce(d.order_date, o.order_date) as order_date,
    d.revenue as daily_revenue,
    o.revenue as orders_revenue
from {{ ref('daily_revenue') }} d
full outer join orders_by_day o
    on d.order_date = o.order_date
where d.orders is distinct from o.orders
   or abs(coalesce(d.revenue, 0) - coalesce(o.revenue, 0)) > 0.005
```

On the current data, the `not_null` test fails on payments 30 and 55. The reconciliation test fails for every Jan to Mar date with a NULL payment, order 25's date (2018-01-28) included.

Minor, unrelated issue: `models/schema.yml` documents a `total_order_amount` column on `customers`, but the model outputs `customer_lifetime_value`.
