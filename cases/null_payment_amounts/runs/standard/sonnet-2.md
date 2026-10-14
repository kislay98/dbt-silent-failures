claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 25 s | USD 0.090 | 2026-10-14 | project ad0b16b | prompt b1e95fc444b5

## 1. Root cause

**The loaded `raw_payments` table has rows with a NULL `amount`, and the models silently drop them.**

- Visible in the sample: payment 30 (order 25, `coupon`) and payment 55 (order 49, `credit_card`) are NULL. `stg_payments` passes them through as NULL (`amount / 100`).
- In `orders.sql`, `sum(payments.amount)` and the per-method `sum(case ...)` skip NULLs. Order 25 therefore shows the other payments only: 20.00 bank transfer plus 22.00 from other payments gives 42.00. Settlement says 58.00, which implies a missing 16.00 coupon payment.
- `not_null_orders_amount` still passes because the sum of the remaining payments is non-null. `stg_payments` has no `not_null` test on `amount`, so the gap goes unnoticed. This also affects `customers` and `region_revenue`.
- **`daily_revenue.sql` is stale, not correct.** It is incremental and rebuilds only the last 3 days, so older days keep the values computed back when the payments still had amounts.
  - The gap by month: Jan 496 vs 458 (+38), Feb 415 vs 394 (+21), Mar 622 vs 616 (+6), total **65**. That is exactly 1533 − 1468.
  - April matches (139 vs 139) because it is inside the rebuild window, or has no NULL payments.

So the dashboard agrees with finance only because it froze the earlier, complete numbers. I inferred the NULL-to-gap link from the sample and totals; I haven't run it against the full table. The query below confirms it.

```sql
select p.id, p.order_id, p.payment_method, o.order_date
from raw_payments p left join raw_orders o on o.id = p.order_id
where p.amount is null
```
The number of rows it returns, and the months they fall in, should account for the 38/21/6 gaps.

## 2. Fix

1. **Data:** have the ingestion job reload or backfill the missing amounts, in cents, from the processor settlement. For example, payment 30 should be 1600. Then fix whatever produces the NULLs upstream.
2. **Rebuild:** run `dbt build --full-refresh -s daily_revenue+ orders+`, or at least `dbt build -s orders+` and a full refresh of `daily_revenue`. After the amounts are restored, `orders` should match settlement (1533.00 for Jan–Mar), and `daily_revenue` should stay equal.
3. **Guard in the model:** don't let NULLs pass quietly. Fail loudly in staging via the test in section 3, rather than using `coalesce(amount, 0)`, which would hide the gap again.

## 3. Tests that would have caught it

Add to `models/staging/schema.yml` under `stg_payments`:

```yaml
      - name: amount
        tests:
          - not_null
```

This fails the build at the source, naming payments 30 and 55.

Add a reconciliation test, `tests/assert_orders_match_payments.sql`:

```sql
-- orders must carry every payment dollar
select o.total, p.total
from (select sum(amount) as total from {{ ref('orders') }}) o,
     (select sum(amount) as total from {{ ref('stg_payments') }}) p
where abs(o.total - p.total) > 0.005
   or o.total is null
```

Add a second test, `tests/assert_daily_revenue_matches_orders.sql`, which catches the stale incremental table:

```sql
select coalesce(d.order_date, o.order_date) as order_date, d.revenue, o.revenue
from {{ ref('daily_revenue') }} d
full join (
    select order_date, sum(amount) as revenue from {{ ref('orders') }} group by 1
) o on d.order_date = o.order_date
where d.revenue is distinct from o.revenue
```

Today this second test would report the stale Jan–Mar days. After the backfill and full refresh it should pass.
