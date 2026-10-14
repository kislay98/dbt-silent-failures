claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 27 s | USD 0.067 | 2026-10-14 | project bef6b69 | prompt f5793c9fa6c4

## 1. Root cause

**The data:** `raw_payments.amount` is NULL on some payment rows, so the file to look at is `seeds/raw_payments.csv`, or whatever the ingestion job loads into that table. The ones visible in the sample are payments 15, 30, 62 and 67, each the last payment row of its order. Order 25 shows this clearly: the processor settled 58.00, and the 16.00 gap is the coupon payment 30, which now has no amount. The `amount` column is an INTEGER holding cents, so the loader has written NULL there.

**Why nothing failed:**
- `models/staging/stg_payments.sql` passes the NULLs through (`amount / 100`).
- `models/orders.sql` uses `sum(...)`, which silently skips NULLs. `amount` stays non-null as long as one payment has a value, and the per-method columns use `case ... else 0`. That is why every `not_null_orders_*` test passes.
- `models/staging/schema.yml` has no `not_null` test on `stg_payments.amount`.

**Why the dashboard still matches settlement:** `models/daily_revenue.sql` is incremental and only rebuilds the last 3 days. Older dates keep the values computed when the payment amounts were still populated. The monthly gaps match that: 496 vs 458, 415 vs 394 and 622 vs 616 differ by 38, 21 and 6, which is 65 in total, and 1533 − 1468 = 65.

So `daily_revenue` has the right history and `orders` has the damaged one. The next full refresh would have broken the dashboard too.

I can't see the seed data, so I haven't confirmed whether the NULLs come from the ingestion job or from the CSV. The log doesn't cover the load step.

## 2. Fix

1. **Repair the source data first.** Re-pull the missing payment amounts from the processor settlement and reload `raw_payments`. For example, payment 30 should be 1600 cents. Do not `coalesce(amount, 0)` in staging, because that would make the totals agree with each other while still being wrong.
2. **Do not `--full-refresh` `daily_revenue` before step 1**, or the dashboard will lose its correct history.
3. Rebuild with `dbt build --full-refresh -s stg_payments+`. After that, `orders.amount` for Jan–Mar should sum to 1533.00 and order 25 should show 58.00.
4. Check the load, and check the ingestion job for why it writes NULL amounts.

Separately, `region_revenue` uses the same payments and has the same understatement. Its regions feed also has an `updated_at` column but no uniqueness test on `customer_id`. If the feed holds more than one row per customer, the join will fan out and inflate revenue, so check that.

## 3. Tests that would have caught it

Source-level check, added to `models/staging/schema.yml`:

```yaml
  - name: stg_payments
    columns:
      - name: amount
        tests:
          - not_null
```

Reconciliation between the two revenue models, as `tests/assert_daily_revenue_matches_orders.sql`:

```sql
select
    coalesce(o.order_date, d.order_date) as order_date,
    o.amount as orders_amount,
    d.revenue as daily_revenue
from (
    select order_date, sum(amount) as amount from {{ ref('orders') }} group by 1
) o
full outer join {{ ref('daily_revenue') }} d
    on o.order_date = d.order_date
where abs(coalesce(o.amount, 0) - coalesce(d.revenue, 0)) > 0.005
```

The first test fails the build on the NULL amounts as soon as they are loaded. The second catches any drift between `orders` and `daily_revenue`, including drift in older dates that the 3-day window never revisits. If you only add one, add the `not_null` on `stg_payments.amount`.
