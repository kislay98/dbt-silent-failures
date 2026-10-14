claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 51 s | USD 0.096 | 2026-10-14 | project ad0b16b | prompt eb0b3a80ab67

## 1. Root cause

The gap is 136.00 in both the March figure (279 − 143) and the year-to-date figure (1103 − 967), so all of it comes from March orders. Two problems in the raw data cause it. The models are not at fault.

**a) A new order status, `delivered`, which the shop counts as completed.**
`raw_orders` now has 8 orders with `status = 'delivered'`, all in March, totalling 134.02 in `orders`. `customers.sql` only counts `status = 'completed'`, so these orders are excluded from `completed_lifetime_value`. The tests didn't catch it because `delivered` was added to the `accepted_values` lists in both `models/staging/schema.yml` and `models/schema.yml`.

**b) Some `raw_payments.amount` values are in dollars, not cents.**
Every normal payment in `raw_payments` is a multiple of 100 (2000, 800, 500…). Some coupon rows are not: id 98 = `23`, id 86 = `2`, and one on order 94 = `7`. `stg_payments.sql` divides every amount by 100, so a $2 coupon becomes 0.02. That is why fractional totals show up only in March and April (coupon 18.27 and 0.33, delivered 134.02, placed 228.56, an order 94 total of 0.07).

The numbers reconcile exactly:
- Delivered: 134.02 includes a 0.02 coupon that should be 2.00, so the true value is 136.00. 143 + 136 = 279 and 967 + 136 = 1103. ✓
- `daily_revenue` is an incremental model that was built before the bad rows arrived and was never reprocessed. It still holds the correct numbers. The differences from `orders` are 622 − 595.27 = 26.73 = 0.99 × 27 for March and 139 − 106.33 = 32.67 = 0.99 × 33 for April. This is exactly what you get when 27 and 33 dollars of coupons are divided by 100. The sum of `daily_revenue` (1672) is the correct total revenue.

This also points to a latent bug in `daily_revenue.sql`: it only rebuilds the last 3 days, so corrections to older payments never propagate. Here that hid the problem, but it will also hide the fix unless you run a full refresh.

## 2. Fix

**Data (preferred):** Fix the ingestion job so coupon amounts are sent in cents, then correct the existing rows. Check them before updating:

```sql
select * from raw_payments where amount % 100 <> 0;   -- expect only the mis-scaled coupon rows
update raw_payments set amount = amount * 100 where amount % 100 <> 0;
```

**Status:** Normalize the status in `models/staging/stg_orders.sql`, keeping the original value for auditing. Confirm with the shop owner that `delivered` means completed; the admin totals say it does.

```sql
        cast(ordered_at as date) as order_date,
        status as source_status,
        case when status = 'delivered' then 'completed' else status end as status
```

Then remove `delivered` from both `accepted_values` lists so any future new status fails the build. Rebuild with `dbt build --full-refresh` so `daily_revenue` is recomputed from corrected data.

Expected after the fix: `completed_lifetime_value` = 1103.00, March completed = 279.00, and `sum(orders.amount)` = `sum(daily_revenue.revenue)` = 1672.00.

## 3. Tests that would have caught this

`tests/assert_payment_amounts_in_cents.sql`:
```sql
-- raw amounts are integer cents; every payment so far has been whole dollars
select * from {{ ref('raw_payments') }}
where amount % 100 <> 0
```

`tests/assert_daily_revenue_matches_orders.sql`:
```sql
-- the incremental model must agree with a full recompute from orders
with expected as (
    select order_date, round(sum(amount), 2) as revenue from {{ ref('orders') }} group by 1
)
select *
from expected
full outer join {{ ref('daily_revenue') }} actual using (order_date)
where expected.revenue is distinct from round(actual.revenue, 2)
```

Also keep `accepted_values` strict. A new upstream status should fail the build and get a deliberate mapping decision, not be added to the allowed list.
