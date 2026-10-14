claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 31 s | USD 0.099 | 2026-10-14 | project ad0b16b | prompt 0f146dda0451

## 1. Root cause

**Some coupon rows in `raw_payments` (the seed/ingestion feed) are in whole dollars, not cents.** `models/staging/stg_payments.sql` divides every row by 100 (`amount / 100 as amount`), so those rows come out 100x too small.

I couldn't see the seed rows, so this is inferred from the samples and the arithmetic:

- Normal rows are whole-dollar amounts stored in cents (`2000`, `800`, `500`, `2600`). A few coupon rows are not: order 86 has `23`, order 76 has `2`, and order 94 shows `0.07` in `orders` (raw `7`). These are the fractional `coupon_amount` values (`0.07`, `0.33`, `18.27`).
- Let X be the dollar-valued coupon rows and C the correct ones. For March, C + X/100 = 18.27 and C + X = 45.00, so X = 27 and C = 18.00. For April, C + X/100 = 0.33 and C + X = 33.00, so X = 33 and C = 0.
- The shortfall equals the settlement gap. For March, 622.00 − 595.27 = 26.73 = 27 × 0.99, and for April, 139.00 − 106.33 = 32.67 = 33 × 0.99. So the coupon rows account for the full discrepancy.
- `daily_revenue` shows 622.00 and 139.00, which match the settlement. It is incremental and only rebuilds the last 3 days, so it kept the old, correct values. `orders` is a full table and picked up the bad rows. The two models disagree, but no test compares them.
- All 28 data tests pass because they only check not-null, unique and accepted values. A coupon of 0.07 is not null, so `not_null_orders_coupon_amount` passes. Nothing checks units or reconciles totals.

## 2. Fix

1. **Fix the data at the source.** Reload the affected `raw_payments` coupon rows in cents (multiply by 100) or have the ingestion job fix its unit handling. To find the rows:
   ```sql
   select * from raw_payments where amount % 100 <> 0;
   ```
   Confirm with marketing's ledger that they total 27 (March) and 33 (April).
2. **Optional stopgap in `stg_payments.sql`, only until the feed is fixed.** It is a heuristic: a dollar-valued row of exactly 100, 200 and so on would still be wrong.
   ```sql
   case
       when amount % 100 <> 0 then amount        -- already in dollars (bad feed rows)
       else amount / 100
   end as amount
   ```
3. **Rebuild, including the stale incremental model:**
   ```
   dbt build --full-refresh
   ```
   After this, March coupon should be 45.00, April 33.00, and March `amount` 622.00.

## 3. Tests

**Unit check on the raw feed.** Add `tests/assert_raw_payments_in_cents.sql`; any returned row fails:
```sql
select id, order_id, payment_method, amount
from {{ ref('raw_payments') }}
where amount % 100 <> 0
```

**Reconciliation between the two models**, which would have flagged this immediately. Add `tests/assert_orders_match_daily_revenue.sql`:
```sql
with o as (
    select order_date, round(sum(amount), 2) as amount from {{ ref('orders') }} group by 1
)
select o.order_date, o.amount, d.revenue
from o
full join {{ ref('daily_revenue') }} d using (order_date)
where abs(coalesce(o.amount, 0) - coalesce(d.revenue, 0)) > 0.005
```

Also add a source-to-staging check: `sum(raw_payments.amount)/100` should equal `sum(stg_payments.amount)`. And if you have the processor settlement or ledger totals available, reconcile monthly totals against them.
