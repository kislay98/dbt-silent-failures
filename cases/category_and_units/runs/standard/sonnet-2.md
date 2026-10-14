claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 66 s | USD 0.140 | 2026-10-14 | project ad0b16b | prompt eb0b3a80ab67

## 1. Root cause

There are two independent defects. Together they explain the whole gap: 967 + 136 = 1103, and 143 + 136 = 279, with all 136 sitting in March.

**A. `delivered` orders are never counted as completed** (`models/customers.sql`, `models/staging/stg_orders.sql`, both `schema.yml` files)
- The source started emitting a new terminal status, `delivered`, in March. The warehouse has 8 such orders worth 134.02.
- `customers.sql` only counts `orders.status = 'completed'` as `completed_lifetime_value`, so those orders are excluded.
- Someone added `'delivered'` to both `accepted_values` lists. That made the new status pass the tests instead of surfacing it, which is why the build is green.
- `delivered` accounts for almost all of the 136 shortfall. The remaining 1.98 is defect B.

**B. Some coupon payments are loaded in dollars, not cents** (`raw_payments`, handled wrongly in `models/staging/stg_payments.sql`)
- `stg_payments` divides every `amount` by 100. Historically every raw amount is a whole multiple of 100, for example 2000 or 2600.
- Newer coupon rows arrive in whole dollars: `id 98` has 23, `id 86` has 2, and April has 33 in total. After `/100` they become 0.23, 0.02 and so on, which is why `orders` shows odd values like `coupon_amount = 0.07` and `18.27`.
- Fixing the unit on payment 86 (coupon 2 → 2.00) lifts delivered from 134.02 to exactly 136.00. That matches the gap. I haven't confirmed that payment 86 belongs to a delivered order; the sample doesn't show it.
- `daily_revenue` confirms the unit problem. Its incremental filter only rebuilds the last 3 days, so older rows are stale from before the feed changed. It shows March = 622 and April = 139, against 595.27 and 106.33 in `orders`. The 26.73 and 32.67 differences are exactly what the small coupon rows lose in the `/100`.
- `orders`, `customers` and `region_revenue` all total 1612.60, so they agree with each other while all being wrong. `daily_revenue` is the only model that is internally inconsistent.

## 2. Fix

Ask the ingestion owner to fix the coupon unit at the source. Meanwhile, as a stopgap in staging:

```sql
-- stg_payments.sql
-- `amount` is stored in cents, except coupon rows from the newer feed, which arrive in whole dollars
case
    when payment_method = 'coupon' and amount % 100 <> 0 then amount
    else amount / 100.0
end as amount
```

Add an explicit fulfilment flag instead of overloading `status`:

```sql
-- stg_orders.sql
status,
status in ('completed', 'delivered') as is_completed
```

```sql
-- customers.sql, in customer_payments
sum(case when orders.is_completed then amount else 0 end) as completed_amount
```

Also expose `is_completed` from `orders.sql` and update the `orders_status` doc. Confirm with finance that `delivered` counts as completed. The numbers say it does, but it is a business definition.

Then rebuild, because the stale history in `daily_revenue` won't repair itself:

```
dbt build --full-refresh -s stg_payments+ 
```

After the rebuild, the completed total should be 1103.00 overall and 279.00 for March. The stopgap rule is a guess based on the whole-dollar pattern, so check it against the raw feed.

## 3. Tests that would have caught it

Unit check on the raw feed (`tests/assert_payment_amounts_are_cents.sql`). This fails on today's data:

```sql
select id, payment_method, amount
from {{ ref('raw_payments') }}
where amount % 100 <> 0
```

Incremental drift check (`tests/assert_daily_revenue_matches_orders.sql`):

```sql
with d as (select order_date, revenue from {{ ref('daily_revenue') }}),
o as (select order_date, sum(amount) as revenue from {{ ref('orders') }} group by 1)
select * from o full join d using (order_date)
where abs(coalesce(o.revenue,0) - coalesce(d.revenue,0)) > 0.005
```

Status coverage: remove `delivered` from the `accepted_values` lists unless it is deliberately mapped, and add a test that every status is classified as completed or not. An unexpected new status should fail the build, not be added to the allowed list.
