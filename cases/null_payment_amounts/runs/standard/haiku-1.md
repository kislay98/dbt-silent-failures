claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 46 s | USD 0.008 | 2026-10-14 | project ad0b16b | prompt b1e95fc444b5

## 1. Root cause

Payment amounts are going missing in the raw load, and the models drop them without complaint. The dashboard is stale, and that's why it still matches the settlement.

**Where the data is lost:**
- `raw_payments` has NULL `amount` values. In the sample, payment `id 30` (order 25, coupon) is NULL. The 58.00 vs 42.00 gap for order 25 is exactly 16.00, which is the value that payment should carry.
- `models/staging/stg_payments.sql` passes NULLs straight through (`amount / 100` on NULL is NULL). Its `schema.yml` has no `not_null` test on `amount`.
- `models/orders.sql` aggregates with `sum(payments.amount)`, which silently skips NULLs. So order 25 becomes 42.00. The `orders.amount` `not_null` test still passes because order 25 has a non-NULL bank_transfer payment (20.00).

The 65.00 Jan–Mar gap is the total of the NULLed payments. Settlement 1533.00 minus orders 1468.00 equals 65.00.

**Why the dashboard disagrees with `orders`:**
- `models/daily_revenue.sql` is incremental with `delete+insert`, and it only rebuilds the last 3 days. Older days keep the values from when the payments were populated.
- Jan–Mar `daily_revenue` sums to 496 + 415 + 622 = 1533.00, which is the settlement figure. Across all months it totals 1672.00, while `orders` totals 1607.00.
- The dashboard is right for these days only by accident. It is a snapshot from before the NULLs appeared. A full refresh today would bring it down to 1468.00 as well.

**Why the tests passed:** No test checks payment-level amounts, and nothing reconciles `daily_revenue` against `orders`. The incremental model also never revisits older days, so the mismatch can't surface in the test results.

**Verification queries** (I haven't run these; the sample only shows 10 rows of `raw_payments`):
```sql
select id, order_id, payment_method from raw_payments where amount is null;
select order_date, revenue from daily_revenue where order_date < current_date - 3;
```
The first should list the NULL payments behind the 65.00. Confirm that their processor amounts sum to 65.00 for Jan–Mar.

## 2. Fix

**Data (the actual correction):** Reload the NULL `raw_payments.amount` values from the processor settlement, then rebuild. Fixing `raw_payments` is the only thing that restores correct totals. Changing the models alone would just make the NULLs fail loudly, or disappear from the totals.

**Code:**

- `models/staging/schema.yml`: add a `not_null` test on `stg_payments.amount` so a NULL fails the build.
- `models/daily_revenue.sql`: drop the incremental config and the `is_incremental()` block, and materialize as a table. It has about 70 rows, so a full rebuild is cheap, and it removes the stale-history problem:
  ```sql
  {{ config(materialized='table') }}
  ```
  If you want to keep it incremental, the lookback has to cover the full history, or it needs a periodic `--full-refresh`.
- `models/staging/schema.yml`: the `not_null` test above would fail the build right now, so the rebuild will need the raw data fixed first.

## 3. Test that would have caught it

Two tests, either of which would have failed this build.

**Column test** (catches the NULLs directly), in `models/staging/schema.yml`:
```yaml
  - name: stg_payments
    columns:
      - name: amount
        tests:
          - not_null
```

**Singular reconciliation test**, `tests/assert_daily_revenue_matches_orders.sql`. This catches the stale-history drift:
```sql
with orders_by_day as (

    select order_date, sum(amount) as amount
    from {{ ref('orders') }}
    group by 1

)

select
    coalesce(d.order_date, o.order_date) as order_date,
    d.revenue,
    o.amount

from {{ ref('daily_revenue') }} d
full outer join orders_by_day o
    on d.order_date = o.order_date

where d.revenue is null
   or o.amount is null
   or abs(d.revenue - o.amount) > 0.005
```

**Also worth noting:** the build log shows 28 data tests, but the project defines 32. The four missing ones are the seed tests on `raw_customer_regions` and `raw_payment_methods`, so they aren't running in this pipeline. The `fee_rate` test in particular should run in the nightly build.
