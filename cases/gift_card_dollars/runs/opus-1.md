claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 41 s | USD 0.189 | 2026-10-14 | project f769fdf | prompt 9b72a4437358

## 1. Root cause

**File:** `jaffle_shop/seeds/raw_payments.csv`. All 12 `gift_card` rows are recorded in **dollars**, while every other row is in **cents**.

| payment id | order_id | raw amount | should be |
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

`models/staging/stg_payments.sql` applies `amount / 100` to every row. That conversion is correct for cents, but it shrinks these 12 rows 100×.

The numbers match exactly:
- The raw gift card sum is 23+6+23+18+11+6+28+14+3+30+26+17 = **205**. That equals the provider statement (205.00). After `/100` it becomes 2.05.
- The shortfall is 205 − 2.05 = 202.95, and 1469.05 + 202.95 = **1672.00**. That equals the processor settlement.
- Order 9: 23 / 100 = 0.23, which is the 23.00 gift card shown as 0.23.

No test caught this because every test is `not_null`, `unique`, `accepted_values` or `relationships`. None of them checks magnitude or reconciles totals, and 0.23 is a perfectly valid non-null number.

## 2. Fix

Correct the source data so the whole column uses one unit (cents), which is what the staging model expects. In `seeds/raw_payments.csv`:

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

If this were a real upstream feed that really does send gift cards in dollars, I'd raise it with the feed owner rather than patch it in staging. A `case when payment_method = 'gift_card' then amount else amount / 100 end` in `stg_payments.sql` would silently double-correct if upstream ever fixes it. If you must patch in staging, pair it with the tests below so a later change upstream gets noticed.

Then rebuild with a full refresh:

```bash
dbt build --full-refresh
```

`--full-refresh` matters here. `daily_revenue` is incremental and only rebuilds the last 3 days. A plain `dbt build` would leave the wrong revenue in place for 2018-01-12, 01-22, 01-26, 02-17, 03-01, 03-02, 03-05, 03-23 and 03-27. `orders`, `customers` and `region_revenue` are tables, so they correct themselves on rebuild.

After the fix you should see: `sum(orders.amount)` = 1672.00, `sum(gift_card_amount)` = 205.00, and order 9 `amount` = 23.00.

## 3. Tests that would have caught it

**a) Unit sanity check on the raw data.** This is cheap and catches this class of bug. A non-zero payment under 100 cents ($1) is almost certainly a dollars-vs-cents error. All 12 bad rows are ≤ 30.

`tests/assert_payment_amounts_look_like_cents.sql`:
```sql
-- Payments are stored in cents. A non-zero payment under 100 (i.e. < $1)
-- almost always means a row was loaded in dollars.
select id, order_id, payment_method, amount
from {{ ref('raw_payments') }}
where amount > 0
  and amount < 100
```

**b) Reconciliation against control totals.** This is the test that matches what finance actually checked. Add a seed of external statement totals, `seeds/payment_control_totals.csv`:
```csv
source,expected_total
processor_settlement,1672.00
gift_card_statement,205.00
```

`tests/assert_orders_reconcile_to_statements.sql`:
```sql
with actual as (

    select 'processor_settlement' as source, sum(amount) as actual_total from {{ ref('orders') }}
    union all
    select 'gift_card_statement', sum(gift_card_amount) from {{ ref('orders') }}

)

select
    controls.source,
    controls.expected_total,
    actual.actual_total
from {{ ref('payment_control_totals') }} as controls
join actual on controls.source = actual.source
where abs(controls.expected_total - actual.actual_total) > 0.01
```

Against the current build, test (a) returns the 12 rows above and test (b) returns both sources, so `dbt build` would have failed instead of reporting PASS=40.

A side note, separate from this bug: `models/schema.yml` documents a `customers.total_order_amount` column that doesn't exist. The model outputs `customer_lifetime_value`.
