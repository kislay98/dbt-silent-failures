claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 42 s | USD 0.137 | 2026-10-14 | project ad0b16b | prompt e23be30fc7e7

## 1. Root cause

`seeds/raw_payments.csv` mixes units in one column. `models/staging/stg_payments.sql` assumes every row is in cents and divides all of them by 100.

- Credit card, coupon and bank transfer rows are in cents (e.g. id 8: `2300` → 23.00).
- The 12 `gift_card` rows are in whole dollars (the gift card provider's unit). The `/ 100` turns them into cents-of-a-dollar:

| payment id | order | raw | staged |
|---|---|---|---|
| 9 | 9 | 23 | 0.23 |
| 22 | 19 | 6 | 0.06 |
| 26 | 23 | 23 | 0.23 |
| 48 | 43 | 18 | 0.18 |
| 49 | 44 | 11 | 0.11 |
| 67 | 58 | 6 | 0.06 |
| 68 | 59 | 28 | 0.28 |
| 71 | 62 | 14 | 0.14 |
| 92 | 80 | 3 | 0.03 |
| 99 | 87 | 30 | 0.30 |
| 107 | 93 | 26 | 0.26 |
| 110 | 96 | 17 | 0.17 |

The raw total is 205 and the staged total is 2.05. The gap is 202.95, which is exactly 1672.00 − 1469.05.

The error flows into `orders.gift_card_amount`, `orders.amount`, `customers.customer_lifetime_value`, `region_revenue.revenue` and, in part, `daily_revenue`.

`daily_revenue` is a second, separate symptom:

- The model is incremental and only rebuilds `order_date >= max(order_date) - 3 days`, which is 2018-04-06 onward.
- Rows before that are stale from an earlier run that had the right gift card values. Jan 496.0 = 444.52 + 52 − 0.52, and Feb and Mar reconcile the same way.
- April is the only month that mixes stale-correct rows (orders 92–95, including the 26.00 gift card on order 93) with rows rebuilt from the bad staging (orders 96–99). It shows 122.17 against a correct 139.00.
- Revenue by month therefore doesn't match `orders` in either direction, and the sum of `daily_revenue` is 1655.17, which matches neither figure.

All 44 checks passed because none of them test values or units. They only check `not_null`, `unique`, `accepted_values` on the method name, and `relationships`. Nothing compares totals to an external control figure.

## 2. Fix

In `models/staging/stg_payments.sql`, replace the `amount` line:

```sql
        -- `amount` is stored in cents, except gift_card rows, which the gift card
        -- provider reports in whole dollars
        case
            when payment_method = 'gift_card' then amount
            else amount / 100
        end as amount
```

Because the source feed is inconsistent, this belongs in staging. Editing the seed would be overwritten by the next load. If the provider fixes the feed, remove the `case`.

Rebuild with a full refresh of the incremental model, so no stale rows survive:

```
dbt build --full-refresh
```

Expected result: `sum(gift_card_amount)` = 205.00, `sum(amount)` = 1672.00, order 9 = 23.00, and `daily_revenue` matches `orders` by month.

## 3. Test that would have caught it

A row-level check like `amount * 100 = raw amount` wouldn't catch this, because the bug is self-consistent. The test has to compare against numbers from outside the project, such as the provider statements.

`tests/assert_orders_match_provider_statements.sql`:

```sql
-- control totals come from the gift card provider statement and processor settlement
select 'gift_card' as check_name, 205.00 as expected, round(sum(gift_card_amount), 2) as actual
from {{ ref('orders') }}
having abs(round(sum(gift_card_amount), 2) - 205.00) > 0.005

union all

select 'total', 1672.00, round(sum(amount), 2)
from {{ ref('orders') }}
having abs(round(sum(amount), 2) - 1672.00) > 0.005
```

For ongoing use, move the control figures into a `provider_control_totals` seed with one row per payment method and compare per method, so the test doesn't need hand edits each month.

Add a second test that `sum(daily_revenue.revenue)` equals `sum(orders.amount)`. That would have caught the stale incremental rows.

I haven't run any of this, since the project files aren't on disk here. The diagnosis comes from reconciling the numbers you pasted.

One small unrelated issue: `models/schema.yml` documents `customers.total_order_amount`, but the actual column is `customer_lifetime_value`.
