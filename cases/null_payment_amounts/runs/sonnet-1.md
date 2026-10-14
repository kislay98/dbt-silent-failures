claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 25 s | USD 0.066 | 2026-10-14 | project bef6b69 | prompt f5793c9fa6c4

## 1. Root cause

**`seeds/raw_payments.csv`** (the raw `raw_payments` table the ingestion job loads) has rows with a NULL `amount`. From the sample, these are payments 15, 30, 62 and 67. In each case it is the last payment row of an order, and the order is understated by exactly that payment:

- Order 25 shows 42.00 (credit card 22 + bank transfer 20) but settled for 58.00. The NULL coupon payment 30 should be 16.00.
- Orders 13, 54 and 58 each have a NULL payment on a second method (bank_transfer, bank_transfer and gift_card).

Nothing in dbt catches this:
- `stg_payments.sql` just does `amount / 100`, and NULL / 100 is NULL.
- `orders.sql` uses `sum(...)`, which silently skips NULLs, and the `case ... else 0` makes the per-method columns 0 instead of NULL. That is why every `not_null` test on `orders.*_amount` and `amount` passes.
- `models/staging/schema.yml` has no `not_null` test on `stg_payments.amount`.

**Why `daily_revenue` still agrees with the settlement.** It is incremental with `delete+insert`, and it only rebuilds `max(order_date) - 3 days`. Every day before the last three keeps the values it computed when the payments still had amounts. Today's `orders` reads the NULLed `raw_payments`, so the two diverge. The differences are Jan 38, Feb 21 and Mar 6, which sum to 65.00, exactly 1533 − 1468. So `daily_revenue` is stale but right, and `orders` is current but wrong.

I haven't seen the seed file or the ingestion job, so why the amounts became NULL (a bad reload or a source change) is inferred. The pattern is consistent with it.

## 2. Fix

Order matters here:

1. **Back up `daily_revenue` first**, for example `create table daily_revenue_bak as select * from daily_revenue`. It currently holds the only correct numbers.
2. **Repair the source.** Re-ingest `raw_payments` with the real amounts in cents, or patch the NULL rows, e.g. payment 30 → `1600`. Don't `coalesce(amount, 0)` in staging, because that hides the problem.
3. Run `dbt build --select stg_payments+`, then `dbt run --full-refresh --select daily_revenue`. Do this only after step 2, otherwise the refresh overwrites the correct history with the broken numbers.
4. Check that `orders` sums to 1533.00 for Jan–Mar and that order 25 shows 58.00.
5. Make `daily_revenue` robust. A fixed three-day window anchored on `max(order_date)` can never pick up a payment that lands later. Either widen the lookback or key it on a load timestamp, and schedule a periodic full refresh.

While in the data, also check `raw_customer_regions`. It has an `updated_at` column but is described as "one row per customer". If any customer has several rows, `region_revenue` fans out and double-counts. Its tests only check uniqueness of `region`, so they wouldn't notice.

## 3. Tests that would have caught it

In `models/staging/schema.yml`:

```yaml
      - name: stg_payments
        columns:
          - name: amount
            tests:
              - not_null
```

In `models/schema.yml`, for the seed:

```yaml
      - name: customer_id
        tests:
          - not_null
          - unique
```

A reconciliation test, `tests/assert_orders_match_daily_revenue.sql`, which fails today on all three months:

```sql
select
    o.order_date,
    o.amount as orders_amount,
    d.revenue as daily_revenue
from (select order_date, sum(amount) as amount from {{ ref('orders') }} group by 1) o
full join {{ ref('daily_revenue') }} d using (order_date)
where abs(coalesce(o.amount, 0) - coalesce(d.revenue, 0)) > 0.005
```

The `not_null` test on `stg_payments.amount` fails at the source and would have caught this on the first bad load. The reconciliation test is a backstop for any drift between the incremental and full-rebuild paths.
