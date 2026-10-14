claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 52 s | USD 0.235 | 2026-10-14 | project ad0b16b | prompt eb0b3a80ab67

# Root cause: two separate problems

Both problems are needed to explain finance's numbers exactly. All tests pass because the tests only check nulls, uniqueness and accepted values, and none of them check whether the amounts are correct.

## 1. Root cause

### A. Some rows in `raw_payments` were loaded in dollars instead of cents

`stg_payments.sql` divides `amount` by 100 because the column is documented as cents. Every legitimate payment is a whole-dollar number of cents (800, 2000, 2600, ...). A few coupon rows hold dollar values instead:

| payment id | order | raw amount | becomes after `/100` | should be |
|---|---|---|---|---|
| 98 | 86 | 23 | 0.23 | 23.00 |
| 86 | 76 | 2 | 0.02 | 2.00 |
| ? | 94 | 7 | 0.07 | 7.00 |

These rows explain the fractional `coupon` totals in `orders`: 18.27 in March and 0.33 in April. `daily_revenue` confirms it. It is incremental, so its older days were built before the bad load and kept the correct values:

- March: 622 − 595.27 = 26.73, which is 27 − 0.27.
- April: 139 − 106.33 = 32.67, which is 33 − 0.33.

So the correct grand total is 1672. `orders` and `customers` currently show 1612.60.

### B. The new `delivered` status is not counted as completed

`customers.sql` counts only `orders.status = 'completed'` toward `completed_lifetime_value`. Since March, completed orders also arrive as `delivered`: 8 orders, all in March. Someone added `delivered` to both `accepted_values` lists in `models/schema.yml` and `models/staging/schema.yml`. That let the new value through, but nothing downstream was taught what it means.

The two fixes together reproduce the admin figures exactly. Fix A turns delivered's 134.02 into 136.00, because order 76's 0.02 coupon becomes 2.00. Then:

- Since January: 967 + 136 = **1103** ✔
- March: 143 + 136 = **279** ✔

Neither fix on its own matches. Including `delivered` without fix A gives 1101.02. Fix A without `delivered` leaves completed at 967, because no completed order has a bad coupon row.

I don't have the `orders_status` doc block or the seed CSVs. Please confirm with the shop team that `delivered` counts as completed, although the exact match makes that very likely.

## 2. Fix

**Data (the actual root cause).** Have the ingestion job reload `raw_payments` in cents. As a stopgap, after reviewing the affected rows:

```sql
-- review first
select * from raw_payments where amount % 100 <> 0;
-- then
update raw_payments set amount = amount * 100 where amount % 100 <> 0;
```

**Code: put the definition of "completed" in one place.** In `models/staging/stg_orders.sql`:

```sql
        cast(ordered_at as date) as order_date,
        status,
        status in ('completed', 'delivered') as is_completed
```

In `models/customers.sql`:

```sql
        sum(case when orders.is_completed then amount else 0 end) as completed_amount
```

**Rebuild:**

```
dbt build --full-refresh -s daily_revenue+ orders+ customers+
```

`daily_revenue` only rebuilds its last 3 days, so it needs `--full-refresh` to pick up any corrected history.

## 3. Tests that would have caught this

**Reconciliation between models.** This would have failed today: March is 595.27 in `orders` but 622 in `daily_revenue`. Save as `tests/assert_daily_revenue_matches_orders.sql`:

```sql
with o as (
    select order_date, round(sum(amount), 2) as revenue from {{ ref('orders') }} group by 1
)
select d.order_date, d.revenue, o.revenue as orders_revenue
from {{ ref('daily_revenue') }} d
full outer join o using (order_date)
where abs(coalesce(d.revenue, 0) - coalesce(o.revenue, 0)) > 0.005
```

**Unit sanity check on raw payments.** Fails on rows like 23 or 2 that are not whole cents-of-dollars. Save as `tests/assert_payments_in_whole_cents_of_dollars.sql`:

```sql
select * from {{ ref('raw_payments') }} where amount % 100 <> 0 or amount <= 0
```

If fractional-dollar prices are legitimate in your shop, use `dbt_utils.accepted_range` with `min_value: 1` on `stg_payments.amount` instead.

**Status coverage.** This makes a new status fail loudly instead of being silently excluded from "completed". Save as `tests/assert_every_status_classified.sql`:

```sql
select status from {{ ref('stg_orders') }}
where status not in ('completed', 'delivered',              -- completed
                     'placed', 'shipped', 'return_pending', 'returned')  -- not completed
```

The rule going forward: when you extend an `accepted_values` list, update this classification in the same change.

Separately, `models/schema.yml` documents a `total_order_amount` column on `customers`, but the model outputs `customer_lifetime_value`. That doc entry is stale and worth fixing while you're there.
