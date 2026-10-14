claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 34 s | USD 0.185 | 2026-10-14 | project ad0b16b | prompt b1e95fc444b5

# Root cause: NULL payment amounts in `raw_payments`, with a stale incremental model hiding the problem

## 1. Root cause

**The bad data is in the `raw_payments` table.** Some payment rows have lost their `amount` and are now `NULL`. Two are visible in the sample:

| payment id | order_id | method | amount |
|---|---|---|---|
| 30 | 25 | coupon | **NULL** |
| 55 | 49 | credit_card | **NULL** |

**How the NULLs flow through `models/orders.sql`:**

- `stg_payments.sql` passes NULL through, since `NULL / 100` is NULL.
- In `orders.sql`, `sum(case when payment_method = 'coupon' then amount else 0 end)` and `sum(payments.amount)` both skip NULLs. The payment silently disappears.
- Order 25 therefore shows only its 20.00 bank transfer plus its other payments, totalling 42.00. The missing coupon payment is the 16.00 difference from the 58.00 settled.
- `customers` and `region_revenue` read the same `stg_payments`, so they are understated too. `customers` lifetime value is 1607, which equals the `orders` total.

**Why `daily_revenue` still matches the settlement:**

- It is incremental (`delete+insert`) and only rebuilds `order_date >= max(order_date) - 3 days`.
- Its January to March rows were built before the amounts went NULL, and they are never recomputed.
- January to March sums to 496 + 415 + 622 = **1533**, which is the settlement figure.
- The per-month gaps between `daily_revenue` and `orders` are 38 (Jan), 21 (Feb) and 6 (Mar), for 65 in total. These are exactly the lost payment amounts.
- April is inside the rebuild window and has no NULLs, so both models agree at 139.
- `daily_revenue` is right only because it is stale. **Do not run `--full-refresh` before fixing the data.** That would overwrite the only correct copy of the numbers.

**Why every test passed:**

- Nothing tests `stg_payments.amount` or `raw_payments.amount` for NULLs.
- The `not_null` tests on `orders.amount` and the `*_amount` columns cannot fire. Each order still has other non-NULL payments, and the `else 0` branch turns non-matching rows into 0, so the sums are never NULL.

## 2. Fix

Fix the data, not the model. Wrapping the amount in `coalesce(amount, 0)` would make the wrong totals permanent.

**a. Find every affected row:**
```sql
select * from raw_payments where amount is null order by order_id;
```

**b. Restore the amounts** from the processor settlement file or the ingestion source, and re-run the load.

- Payment 30 (order 25, coupon) should be `1600` cents.
- `daily_revenue` can help recover the rest. For each day, the missing amount is `daily_revenue.revenue - sum(orders.amount)`:
  ```sql
  select d.order_date, d.revenue - sum(o.amount) as missing
  from daily_revenue d join orders o using (order_date)
  group by d.order_date, d.revenue
  having d.revenue <> sum(o.amount);
  ```

**c. Rebuild** with `dbt build --full-refresh`. Then check that `orders.amount` for January to March totals 1533 and that order 25 shows 58.00.

**d. Fix the ingestion job** so it fails on, or quarantines, payments without an amount instead of loading NULLs.

## 3. Tests that would have caught this

**Column test** in `models/staging/schema.yml`:

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
        # ... existing accepted_values test
```

The same `not_null` test can go on `raw_payments.amount` under `seeds:` in `models/schema.yml`.

**Reconciliation test** `tests/assert_daily_revenue_matches_orders.sql`. This catches any drift between the incremental model and its source, whatever the cause:

```sql
-- daily_revenue is incremental; it must still agree with a fresh aggregate of orders
with fresh as (

    select order_date, count(*) as orders, sum(amount) as revenue
    from {{ ref('orders') }}
    group by order_date

)

select
    coalesce(d.order_date, f.order_date) as order_date,
    d.orders as daily_orders,
    f.orders as fresh_orders,
    d.revenue as daily_revenue,
    f.revenue as fresh_revenue
from {{ ref('daily_revenue') }} d
full outer join fresh f
    on d.order_date = f.order_date
where d.order_date is null
   or f.order_date is null
   or d.orders <> f.orders
   or abs(coalesce(d.revenue, 0) - coalesce(f.revenue, 0)) > 0.005
```

On last night's build, the `not_null` test would have failed on payments 30 and 55. The reconciliation test would have flagged every January to March day with a missing payment.
