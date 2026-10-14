claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 36 s | USD 0.249 | 2026-10-14 | project ad0b16b | prompt e23be30fc7e7

# Root cause: gift card rows in `raw_payments.csv` are in dollars, not cents

## 1. Root cause

**File:** `jaffle_shop/seeds/raw_payments.csv`, all 12 `gift_card` rows.

`stg_payments.sql` treats every `amount` as cents and divides by 100. The gift card rows hold dollar values instead. This is the only unit mismatch in the file.

| payment id | order_id | stored | meant (cents) |
|---|---|---|---|
| 9 | 9 | 23 | 2300 |
| 22 | 19 | 6 | 600 |
| 26 | 23 | 23 | 2300 |
| 48 | 43 | 18 | 1800 |
| 49 | 44 | 11 | 1100 |
| 67 | 58 | 6 | 600 |
| 68 | 59 | 28 | 2800 |
| 71 | 62 | 14 | 1400 |
| 92 | 80 | 3 | 300 |
| 99 | 87 | 30 | 3000 |
| 107 | 93 | 26 | 2600 |
| 110 | 96 | 17 | 1700 |

These rows explain every discrepancy finance reported:

- **Gift card total:** the stored values sum to 205, which is exactly the provider statement (205.00). After `/100` it becomes 2.05.
- **Settlement total:** 1469.05 − 2.05 + 205.00 = **1672.00**, which is exactly the processor settlement.
- **Order 9:** 23 / 100 = 0.23.

**Evidence this is a recent data change, not a long-standing bug:** the incremental `daily_revenue` table still holds correct history.

- January in `daily_revenue` is 496.00. That equals 444.52 − 0.52 + 52.00, so the gift cards were counted at full value.
- April is 122.17, a mix of good and bad data. That equals the correct 139.00 with only order 96 (2018-04-06) wrong (17 → 0.17).
- April 6 falls inside the 3-day rebuild window (`max(order_date) - 3 days`, where the max date is 04-09). Earlier dates were built from a previous load when gift cards were still in cents.

So the gift card feed switched to dollars between loads. `orders`, `customers`, `region_revenue`, and the last 3 days of `daily_revenue` are all affected.

**Why the tests didn't catch it:** every test is `not_null`, `unique`, `accepted_values`, or `relationships`. None of them checks the magnitude or units of `amount`.

## 2. Fix

**Data fix (preferred, since the seed is the source and its contract is cents):** multiply the 12 gift card amounts by 100 in `seeds/raw_payments.csv`:

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

**Alternative if the upstream feed will keep sending gift cards in dollars:** normalise in `models/staging/stg_payments.sql` instead:

```sql
        -- `amount` is stored in cents, except gift_card which the provider feed sends in dollars
        case
            when payment_method = 'gift_card' then amount
            else amount / 100
        end as amount
```

Use only one of the two fixes, not both. After either one, rebuild with a full refresh so `daily_revenue` is consistent:

```
dbt build --full-refresh
```

Expected results afterwards:
- `sum(orders.amount)` = 1672.00
- `sum(gift_card_amount)` = 205.00
- order 9 amount = 23.00
- `daily_revenue` monthly totals of 496 / 415 / 622 / 139, which also sum to 1672

## 3. Tests that would have caught it

**a) Unit sanity on payments.** All 12 bad rows are non-zero and under $1, and no real payment in this data is. Add `tests/assert_payment_amounts_plausible.sql`:

```sql
-- Non-zero payments under $1.00 almost always mean a cents/dollars unit mix-up
select payment_id, order_id, payment_method, amount
from {{ ref('stg_payments') }}
where amount <> 0
  and amount < 1
```

This returns the 12 gift card rows today.

**b) Reconciliation between the incremental model and its source.** This catches upstream restatements that the incremental window hides, which is exactly what happened here. Add `tests/assert_daily_revenue_matches_orders.sql`:

```sql
with expected as (
    select order_date, count(*) as orders, round(sum(amount), 2) as revenue
    from {{ ref('orders') }}
    group by order_date
)

select e.order_date, e.revenue as expected_revenue, d.revenue as actual_revenue
from expected e
full outer join {{ ref('daily_revenue') }} d
    on e.order_date = d.order_date
where e.order_date is null
   or d.order_date is null
   or e.orders <> d.orders
   or abs(e.revenue - round(d.revenue, 2)) > 0.005
```

This fails on almost every date before 04-06 in the current build.

**If you can load the processor and gift card statements as a source,** the strongest test is a direct reconciliation: `sum(orders.<method>_amount)` should equal the statement total per method/processor. That is the check finance did by hand.
