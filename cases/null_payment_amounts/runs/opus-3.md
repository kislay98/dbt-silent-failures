claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 34 s | USD 0.135 | 2026-10-14 | project f769fdf | prompt 2bea65134e32

## 1. Root cause

**Several rows in the warehouse table `raw_payments` have `amount = NULL`.** The models pass these NULLs through, and `sum()` silently skips them.

The sample shows the pattern. Every order in it has one payment row with no amount, and it is always the last payment for that order:

| order | payment_id | method | amount |
|---|---|---|---|
| 13 | 15 | bank_transfer | NULL |
| 25 | 30 | coupon | NULL ← the missing 16.00 (58 − 42) |
| 54 | 62 | bank_transfer | NULL |
| 58 | 67 | gift_card | NULL |

How this flows through the files:

- **`models/staging/stg_payments.sql`** does `amount / 100 as amount`. NULL stays NULL, and nothing tests `stg_payments.amount`.
- **`models/orders.sql`** builds `order_payments` with `sum(amount)` and `sum(case when … then amount else 0 end)`. Both skip NULLs, so order 25 sums to 22 + 20 = 42. `coupon_amount` comes out as 0, not NULL, because the `else 0` rows keep the sum non-null.
- **`models/schema.yml`**: `not_null_orders_amount` and the per-method `not_null` tests pass. A sum over a group with at least one non-null value is never NULL, so these tests cannot see the problem.

**Why `daily_revenue` still matches the settlement:** it is incremental (`delete+insert`) and only rebuilds `max(order_date) - 3 days`. The January–March rows were computed before the amounts went NULL and have not been recomputed since. Only `orders`, a full-rebuild table, shows the damage. The gaps add up exactly:

- Jan: 496 − 458 = 38
- Feb: 415 − 394 = 21
- Mar: 622 − 616 = 6
- Total: 65 = 1533 − 1468

April matches, so either no April payment is NULL or those days were recomputed recently.

The defect is in the **data**, coming from the ingestion job, not in the SQL. The SQL only fails to notice it.

## 2. Fix

**Data, which is the actual fix:** have the ingestion job reload `raw_payments` from the processor. To see the scope first:

```sql
select * from raw_payments where amount is null order by order_id, id;
```

Then rebuild:

```bash
dbt build --select stg_payments+ 
dbt build --select daily_revenue --full-refresh   # only AFTER the reload
```

Run the full refresh only after the reload. If you run it first, the dashboard becomes wrong too, and you lose the only copy in the warehouse that still has the correct historical totals.

**Code, so this cannot pass silently again:** in `models/orders.sql`, make an order's total NULL when any of its payments has no amount. The existing `not_null_orders_amount` test then fails:

```sql
        case when count(*) = count(amount) then sum(amount) end as total_amount
```

## 3. Tests that would have caught it

`models/staging/schema.yml`, which catches the bad rows directly:

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

`tests/assert_daily_revenue_matches_orders.sql`, a reconciliation test. It also catches the incremental table drifting away from its source for any reason:

```sql
with o as (
    select order_date, sum(amount) as revenue from {{ ref('orders') }} group by 1
)
select o.order_date, o.revenue as orders_revenue, d.revenue as daily_revenue
from o
full outer join {{ ref('daily_revenue') }} d using (order_date)
where abs(coalesce(o.revenue, 0) - coalesce(d.revenue, 0)) > 0.005
```

With the current data, this test fails on the January–March dates. The `not_null` test fails on payments 15, 30, 62, 67 and any other NULL rows.

Two unrelated things I noticed while reading:

- `raw_customer_regions` has an `updated_at` column, but there is no `unique` test on `customer_id`. If the feed keeps history, `region_revenue` will fan out and over-count revenue.
- `schema.yml` documents a `customers.total_order_amount` column, but the model outputs `customer_lifetime_value`.
