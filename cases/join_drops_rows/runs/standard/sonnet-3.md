claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 30 s | USD 0.102 | 2026-10-14 | project ad0b16b | prompt 3fdc8e98d7e9

## 1. Root cause

**Payments with a NULL `order_id` in `seeds/raw_payments.csv` are silently dropped, and `daily_revenue` is hiding it with stale data.**

- `raw_payments` has rows with `order_id = NULL`. The visible ones are raw id 91 (credit_card, 900 cents) and stg `payment_id` 15 (bank_transfer, 14.00). `stg_payments.sql` passes them through unchanged.
- Every downstream model inner-joins or left-joins on `order_id`, so these rows count toward `sum(stg_payments.amount)` (1672) and nowhere else:
  - `orders.sql` joins `stg_orders` to `order_payments` on `order_id`.
  - `customers.sql` groups by `orders.customer_id`, so the orphans fall into a NULL group that `customers` never joins to.
  - `region_revenue.sql` uses `join orders on payments.order_id = orders.order_id`, which drops them.
- The three models agree on 1532, and 1672 − 1532 = **140.00** of orphaned payments.
- Order 25 is almost certainly the same case. Its one visible payment is 20.00 (raw id 28), and the 16.00 that makes up the difference between 42 and 58 is a payment that lost its `order_id`. I can't see the seed rows, so confirm this with the query below.
- `daily_revenue.sql` is the tell. Its monthly sums (496 + 415 + 622 + 139) total **1672**, but `orders` totals 1532. It is incremental with `delete+insert` and only rebuilds the last 3 days relative to `max(order_date)` in itself. So older days keep values from an earlier load, when those payments still had their `order_id`. The `order_id` values were nulled upstream afterwards, and nothing flagged it.
- Every test passes because none of them covers this. `stg_payments.order_id` has no `not_null` or `relationships` test, and nothing reconciles totals.

Confirm with:
```sql
select p.payment_id, p.order_id, p.payment_method, p.amount
from stg_payments p
left join stg_orders o on p.order_id = o.order_id
where o.order_id is null;   -- expect amounts summing to 140.00, incl. 16.00 for order 25
```

## 2. Fix

1. **Data:** restore the correct `order_id` on those rows in `raw_payments`. Get the mapping from the source or processor, because it can't be guessed from this data. Do not default them to some order.
2. **`daily_revenue`:** make it a plain table. It is ~70 rows, and the late-payment window is exactly what let it go stale. If you keep it incremental, run `dbt build --full-refresh -s daily_revenue` now.
   ```sql
   {{ config(materialized='table') }}
   ```
   Drop the `is_incremental()` block too.
3. **Optional guard in `stg_payments`:** don't coalesce the NULLs to a fake order. Let the tests below fail loudly instead.

## 3. Tests

Staging tests in `models/staging/schema.yml`, under `stg_payments`:
```yaml
      - name: order_id
        tests:
          - not_null
          - relationships:
              arguments:
                to: ref('stg_orders')
                field: order_id
```

Reconciliation singular tests in `tests/`:
```sql
-- tests/assert_orders_amount_matches_payments.sql
select o.total, p.total
from (select sum(amount) as total from {{ ref('orders') }}) o
cross join (select sum(amount) as total from {{ ref('stg_payments') }}) p
where abs(o.total - p.total) > 0.005
```
```sql
-- tests/assert_downstream_revenue_reconciles.sql
select 1
from (select sum(amount) a from {{ ref('orders') }}) o,
     (select sum(revenue) a from {{ ref('daily_revenue') }}) d,
     (select sum(revenue) a from {{ ref('region_revenue') }}) r
where abs(o.a - d.a) > 0.005 or abs(o.a - r.a) > 0.005
```

The first test, along with the `not_null`/`relationships` tests, would have failed on the 140.00 of orphaned payments. The second would have caught the stale `daily_revenue` on its own.
