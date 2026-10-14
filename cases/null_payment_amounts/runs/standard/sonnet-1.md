claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 27 s | USD 0.094 | 2026-10-14 | project ad0b16b | prompt b1e95fc444b5

## 1. Root cause

**Data:** `raw_payments` (loaded by the ingestion job, not by dbt) contains rows with `amount = NULL`. Two are visible in the sample:
- payment 30: order 25, `coupon`, NULL
- payment 55: order 49, `credit_card`, NULL

**Code:** nothing in the models treats a NULL amount as an error.
- `models/staging/stg_payments.sql` computes `amount / 100`, which stays NULL.
- `models/orders.sql` uses `sum(payments.amount)` and `sum(case ... else 0 end)`. SQL aggregates skip NULLs, so those payments silently contribute 0.
- Order 25 therefore shows 20.00 (bank transfer) plus its other payments = 42.00. Finance settled 58.00, so the NULL coupon payment was probably 16.00. That is inferred from the gap, and the real value has to come from the source.
- `fees`, `customers.customer_lifetime_value` and `region_revenue` are understated for the same reason.

**Why every test passed:** the only amount-related tests are `not_null` on `orders.amount` and the per-method columns. Those are aggregates, and every order has at least one non-NULL payment, so they are never NULL. `stg_payments.amount` has no `not_null` test, and nothing reconciles outputs against each other.

**Why `daily_revenue` still matches the settlement:** it is incremental with `delete+insert`, and it only rebuilds the last 3 days (`where order_date >= max(order_date) - interval 3 day`). Everything older is frozen at the values from earlier runs, from before the NULLs arrived. The gap is 496 vs 458 in January, 415 vs 394 in February and 622 vs 616 in March, which is 65.00 in total and equals 1533 − 1468. April matches (139 vs 139) because it falls inside the rebuilt window. The dashboard agrees with finance only because it holds stale-but-correct history.

I haven't run anything against the warehouse. The stale-history explanation fits all the numbers but should be confirmed with the check in step 2.

## 2. Fix

1. Confirm the scope:
   ```sql
   select * from raw_payments where amount is null;
   ```
2. Fix the ingestion job and backfill the real amounts from the processor or source. For example, payment 30 should probably be `1600` (cents), but verify it. Don't `coalesce(amount, 0)` in staging, since that hides the problem.
3. Rebuild the frozen history. A normal run won't touch it:
   ```
   dbt build --full-refresh -s daily_revenue+ 
   dbt build
   ```
   Consider a scheduled full refresh of `daily_revenue`, or a wider lookback, so stale history can't go unnoticed for long.
4. Add the tests below so a NULL amount fails the build.

## 3. Tests

In `models/staging/schema.yml`, add this to `stg_payments`:
```yaml
      - name: amount
        tests:
          - not_null
```
You could also declare `raw_payments` as a source with the same test, so a bad load fails before any model runs.

Add `tests/assert_daily_revenue_matches_orders.sql`. This is the check that would have caught the divergence:
```sql
select
    coalesce(o.order_date, d.order_date) as order_date,
    o.revenue as orders_revenue,
    d.revenue as daily_revenue
from (
    select order_date, sum(amount) as revenue
    from {{ ref('orders') }}
    group by order_date
) o
full outer join {{ ref('daily_revenue') }} d
    on o.order_date = d.order_date
where coalesce(o.revenue, 0) != coalesce(d.revenue, 0)
```
Run against the current data, it fails on January to March.
