claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 38 s | USD 0.254 | 2026-10-14 | project ad0b16b | prompt e23be30fc7e7

# Gift card payments are in dollars while every other payment is in cents

## 1. Root cause

**File:** `jaffle_shop/seeds/raw_payments.csv`, the 12 `gift_card` rows.

`stg_payments.sql` divides every `amount` by 100 because the raw feed is in cents. Credit card, coupon and bank transfer rows follow that rule (`2000`, `1700`, …). The gift card rows are already in dollars:

| payment id | order_id | raw amount | becomes after `/100` | should be |
|---|---|---|---|---|
| 9 | 9 | 23 | 0.23 | 23.00 |
| 22 | 19 | 6 | 0.06 | 6.00 |
| 26 | 23 | 23 | 0.23 | 23.00 |
| 48 | 43 | 18 | 0.18 | 18.00 |
| 49 | 44 | 11 | 0.11 | 11.00 |
| 67 | 58 | 6 | 0.06 | 6.00 |
| 68 | 59 | 28 | 0.28 | 28.00 |
| 71 | 62 | 14 | 0.14 | 14.00 |
| 92 | 80 | 3 | 0.03 | 3.00 |
| 99 | 87 | 30 | 0.30 | 30.00 |
| 107 | 93 | 26 | 0.26 | 26.00 |
| 110 | 96 | 17 | 0.17 | 17.00 |

The raw values sum to 205, which matches the provider statement of 205.00. After the division they sum to 2.05, which matches what finance sees. The gap is 205.00 − 2.05 = 202.95, and 1469.05 + 202.95 = **1672.00**, the processor settlement total exactly. The monthly `gift_card` column (0.52 / 0.29 / 0.81 / 0.43) is also exactly 1/100 of the per-month raw sums (52 / 29 / 81 / 43).

This error flows into `orders.amount`, `orders.gift_card_amount`, `customers.*_lifetime_value` and `region_revenue.revenue`. Fees are unaffected because `gift_card.fee_rate = 0`.

**Evidence that the units changed recently: `daily_revenue` disagrees with `orders`.** It is incremental and only rebuilds rows where `order_date >= max(order_date) - 3 days`, which here means 2018-04-06 onward. Its older rows were built when gift card amounts were still correct. Jan, Feb and Mar give 496 / 415 / 622, which equals `orders` with gift cards at full value. Only April's 122.17 is wrong: the correct 139 minus 17.00 plus 0.17 from order 96 on 04-06. So the table currently holds a mix of correct and incorrect rows (total 1655.17). That points to the seed or upstream feed changing gift card units between runs, rather than a logic bug in the SQL.

## 2. Fix

There are two options, depending on what the upstream feed actually sends now.

**Option A: the raw data is the mistake.** Restore the gift card amounts to cents in `seeds/raw_payments.csv`:

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

**Option B: the gift card provider's feed now genuinely sends dollars.** Normalise the units in `models/staging/stg_payments.sql` and keep the raw data as delivered:

```sql
        -- `amount` is stored in cents, except gift card payments, which the provider sends in dollars
        case
            when payment_method = 'gift_card' then amount
            else amount / 100
        end as amount
```

Before choosing, confirm with whoever owns the feed which one is true. If the feed changed on a specific date, Option B needs a date condition.

**With either option, run:**

```
dbt build --full-refresh
```

The full refresh makes `daily_revenue` rebuild every date instead of only the last 3 days. Expected results afterwards:
- `sum(orders.amount)` = 1672.00
- `sum(gift_card_amount)` = 205.00
- order 9 `amount` = 23.00
- `daily_revenue` total = 1672.00

## 3. Tests that would have caught it

**a) Reconcile `daily_revenue` with `orders`.** This would have failed on this exact build: 1655.17 vs 1469.05. It also catches incremental drift in general. Add `tests/assert_daily_revenue_matches_orders.sql`:

```sql
-- daily_revenue is incremental; its total must still equal the orders it summarises
select
    o.total as orders_total,
    d.total as daily_revenue_total
from (select round(sum(amount), 2) as total from {{ ref('orders') }}) o
cross join (select round(sum(revenue), 2) as total from {{ ref('daily_revenue') }}) d
where o.total is distinct from d.total
```

**b) Catch amounts in the wrong unit at staging.** Every non-gift-card payment here is either 0 or at least 1.00. A positive payment below 1.00 is almost certainly a units error. Add `tests/assert_stg_payments_amount_plausible.sql`:

```sql
-- sub-dollar payments indicate an amount that was already in dollars before the /100 conversion
select payment_id, payment_method, amount
from {{ ref('stg_payments') }}
where amount > 0 and amount < 1
```

This returns all 12 gift card rows today.

**c) Reconcile against the external statements (strongest, if you can get them).** Load the processor and gift card statement totals as a seed, for example `raw_settlements(payment_method, statement_total)`. Then assert that `sum(stg_payments.amount)` per method equals `statement_total`. Finance found the problem with exactly this comparison, so automating it would have caught it in the build.
