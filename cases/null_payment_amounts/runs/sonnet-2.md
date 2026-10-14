claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 29 s | USD 0.070 | 2026-10-14 | project bef6b69 | prompt f5793c9fa6c4

## 1. Root cause

**The raw payments table has NULL `amount` values, and the models silently drop them.** `daily_revenue` only looks correct because it is a stale snapshot.

- **The data problem is in `raw_payments`, which the ingestion job loads.** `stg_payments` shows payments 15, 30, 62 and 67 with `amount = NULL`. Order 25 has a coupon payment (id 30) with a NULL amount. The settlement of 58.00 is 22 + 20 + 16, so that payment should be 16.00. The orders sample is consistent with this: the NULL rows are all non-credit-card payments, and the order 25 coupon amount shows as 0.0.
- **`orders.sql` hides it.** `sum(amount)` and `sum(case when ... then amount else 0 end)` both skip NULLs without complaint. Order 25 becomes 42.00 instead of 58.00, and the `not_null` tests on `orders.amount` and the `*_amount` columns still pass, because the other payments keep the sums non-NULL.
- **No test covers the source of the problem.** `stg_payments.amount` has no `not_null` test, and `raw_payments` has no source or seed tests at all.
- **`daily_revenue.sql` is why the dashboard still matches settlement.** It is incremental with `delete+insert` and only rebuilds the last 3 days. Older dates were computed when the payment amounts were still populated, and they are never recomputed. Jan–Mar `daily_revenue` is 496 + 415 + 622 = 1533, which is exactly the settlement. `orders` is rebuilt in full every night from the current NULL-bearing data, giving 458 + 394 + 616 = 1468. The 65.00 gap is the sum of the NULL payments. April matches because it falls inside the rebuild window or had no NULL payments.

So `orders` is wrong and `daily_revenue` is only coincidentally right. It would diverge again whenever late-arriving data changes old days.

I haven't run anything against the warehouse. This comes from the pasted samples, so confirm with `select * from raw_payments where amount is null`. The query should return the 4 rows above, plus any others that make up the 65.00.

## 2. Fix

1. **Repair the data.** Backfill the missing `amount` values in `raw_payments` from the processor's settlement export, since the true amounts can't be derived from the project. Order 25's coupon payment (id 30) should be 1600 cents. Then fix the ingestion job so it stops writing NULL amounts.
2. **Rebuild so the models agree again:**
   ```
   dbt build --full-refresh -s daily_revenue+ orders+
   ```
   Running `orders` normally recomputes it, but the full refresh makes `daily_revenue` re-derive all history from `orders` instead of keeping the old snapshot.
3. **Make the pipeline fail loudly instead of coalescing.** Add the tests below. Don't add `coalesce(amount, 0)` in `stg_payments`, because that would hide the bug again.

## 3. Tests that would have caught it

In `models/staging/schema.yml`, under `stg_payments`:

```yaml
      - name: amount
        tests:
          - not_null
```

This alone would have failed the nightly build on the NULL rows. Since `raw_payments` is loaded outside dbt, it's also worth testing it at the source.

A singular test, `tests/assert_daily_revenue_matches_orders.sql`, catches the stale-incremental drift:

```sql
select
    coalesce(d.order_date, o.order_date) as order_date,
    d.revenue as daily_revenue,
    o.amount as orders_amount
from {{ ref('daily_revenue') }} d
full join (
    select order_date, sum(amount) as amount
    from {{ ref('orders') }}
    group by 1
) o on d.order_date = o.order_date
where abs(coalesce(d.revenue, 0) - coalesce(o.amount, 0)) > 0.005
```

This test would have failed for January through March, because `orders` and `daily_revenue` disagreed on those months.

**Side note, not part of this incident:** `raw_customer_regions` is documented as "one row per customer", but there is no `unique` test on `customer_id`. Duplicate rows would inflate `region_revenue` through the join, so add `unique` there.
