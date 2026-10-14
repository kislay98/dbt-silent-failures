claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 66 s | USD 0.012 | 2026-10-14 | project ad0b16b | prompt e23be30fc7e7

## Summary

The gift card rows in `raw_payments` are whole dollars, but every other row is cents. `stg_payments.sql` divides all rows by 100, so gift cards are understated 100x. A second, separate problem is that `daily_revenue` still holds stale values for older days, so it disagrees with `orders` too.

I worked from the pasted files and didn't run anything.

## 1. Root cause

**`jaffle_shop/seeds/raw_payments.csv`: 12 `gift_card` rows are in dollars.** Example, order 9:

```
9,9,gift_card,23
```

The staging comment says `amount` is in cents, so `stg_payments` turns this into 0.23. That flows into `orders.amount` and `orders.gift_card_amount` for order 9. The customer redeemed 23.00.

**`jaffle_shop/models/staging/stg_payments.sql`: `amount / 100 as amount`** applies to every payment method, including gift cards.

The arithmetic confirms it:

| Check | Value |
|---|---|
| Sum of raw `gift_card` rows | 205 (matches the provider's 205.00) |
| `orders.gift_card_amount` | 2.05 = 205 / 100 |
| Non-gift payments, cents / 100 | 1469.05 − 2.05 = 1467.00 |
| Settlement = 1467.00 + 205 (raw gift) | 1672.00 ✓ |

The 202.95 gap between finance's totals and ours is exactly the gift card unit error. I checked the totals, not each payment method against its own processor statement, so the non-gift rows are assumed correct.

**Why every test passed:** the only tests are `not_null`, `unique`, and `accepted_values`. Nothing checks magnitudes, units, or totals against an external source. A 100x error passes all of them.

**Second issue: `jaffle_shop/models/daily_revenue.sql` is stale.** The incremental filter only rebuilds `order_date >= max(order_date) − 3 days`, which is 2018-04-06 in this data. Older days keep whatever values they had when they were written, so the table now mixes unit conventions:

- Jan, Feb, and Mar carry the gift card values as dollars. The monthly excess over `orders` is exactly raw gift minus gift/100: +51.48, +28.71, +80.19.
- 2018-04-03 (order 93, gift 26) shows 33.0. Correct current value is 7.26. Stale.
- 2018-04-06 onward was rebuilt with the cents convention, so order 96's gift 17 is now 0.17. Wrong in the other direction.

A window-based incremental model can't correct history.

## 2. Fix

**a) Correct the source rows.** Multiply the 12 gift card amounts by 100 so the whole `amount` column is in cents, as the staging model assumes:

```csv
9,9,gift_card,2300
22,19,gift_card,600
26,23,gift_card,2300
48,43,gift_card,1800
49,44,gift_card,1100
67,58,gift_card,600
68,59,gift_card,2800
71,62,gift_card,1400
92,80,gift_card,300
99,87,gift_card,3000
107,93,gift_card,2600
110,96,gift_card,1700
```

The new gift total is 20500 cents = 205.00, and order 9 becomes 23.00. Before merging, confirm with the gift card provider that the feed really is dollars. If the upstream feed is wrong, fix it there and reload, rather than hand-editing a seed.

**b) Rebuild with a full refresh** so the stale `daily_revenue` rows are dropped:

```
dbt build --full-refresh
```

**c) Make `daily_revenue` a table** (the project default). At 99 orders, incremental buys nothing, and a table always reflects corrected history. If you keep it incremental, add a lookback that covers all history, or a periodic full refresh.

## 3. Tests that would have caught this

**Reconciliation to the provider statement.** Add a seed of the provider's redemption rows, then a singular test:

```
seeds/raw_gift_card_statement.csv   -- order_id, amount, one row per redemption from the provider
```

```sql
-- tests/assert_gift_card_total_matches_provider.sql
with warehouse as (
    select sum(gift_card_amount) as total from {{ ref('orders') }}
),
provider as (
    select sum(amount) as total from {{ ref('raw_gift_card_statement') }}
)
select warehouse.total as warehouse_total, provider.total as provider_total
from warehouse, provider
where abs(warehouse.total - provider.total) > 0.005
```

**Derived table reconciles to its upstream.** This catches the stale incremental rows:

```sql
-- tests/assert_daily_revenue_matches_orders.sql
with expected as (
    select order_date, sum(amount) as revenue
    from {{ ref('orders') }}
    group by 1
)
select
    coalesce(d.order_date, e.order_date) as order_date,
    d.revenue,
    e.revenue as expected_revenue
from {{ ref('daily_revenue') }} as d
full outer join expected as e on d.order_date = e.order_date
where abs(coalesce(d.revenue, 0) - coalesce(e.revenue, 0)) > 0.005
```

Based on the numbers above, both should fail on the current build and pass after the fix and full refresh. I haven't run them.

**Side note:** `models/schema.yml` documents `total_order_amount` on `customers`, but the model outputs `customer_lifetime_value`. That's documentation drift, not the cause here.

Want me to apply the seed change, the model change, and the two tests to the project?
