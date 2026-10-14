claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 43 s | USD 0.195 | 2026-10-14 | project f769fdf | prompt 9b72a4437358

## 1. Root cause

**File:** `jaffle_shop/seeds/raw_payments.csv`. All 12 `gift_card` rows are recorded in **dollars**, but every other row is in **cents**. `stg_payments.sql` divides every amount by 100 (`amount / 100 as amount`, with the comment that amounts are stored in cents), so each gift card payment ends up 100× too small.

| payment id | order_id | raw amount | should be (cents) |
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

The numbers reconcile exactly:
- The raw gift card values sum to 205, which matches the provider statement of **205.00**. After `/100` they become 2.05.
- The other methods total 411 + 185 + 871 = 1467. Adding 205 gives **1672.00**, the processor settlement total. Adding 2.05 instead gives 1469.05.
- The shortfall is 1672.00 − 1469.05 = **202.95**, which is 205 − 2.05.

No test failed because every test is structural (`unique`, `not_null`, `accepted_values`, `relationships`). None of them checks magnitudes or reconciles against an outside total. A value of 0.23 is non-null and valid as far as those tests can tell.

The error flows into every model built on payments: `orders` (`gift_card_amount` and `amount`), `customers` (lifetime values), `daily_revenue`, and `region_revenue`.

## 2. Fix

Fix the data so the seed follows the "cents" contract that `stg_payments` already assumes. In `seeds/raw_payments.csv`:

```diff
-9,9,gift_card,23
+9,9,gift_card,2300
-22,19,gift_card,6
+22,19,gift_card,600
-26,23,gift_card,23
+26,23,gift_card,2300
-48,43,gift_card,18
+48,43,gift_card,1800
-49,44,gift_card,11
+49,44,gift_card,1100
-67,58,gift_card,6
+67,58,gift_card,600
-68,59,gift_card,28
+68,59,gift_card,2800
-71,62,gift_card,14
+71,62,gift_card,1400
-92,80,gift_card,3
+92,80,gift_card,300
-99,87,gift_card,30
+99,87,gift_card,3000
-107,93,gift_card,26
+107,93,gift_card,2600
-110,96,gift_card,17
+110,96,gift_card,1700
```

Then rebuild. Use `--full-refresh` because `daily_revenue` is incremental and only rebuilds the last 3 days, so the older wrong dates would otherwise stay in the table:

```bash
dbt build --full-refresh
```

The expected results are `sum(amount) = 1672.00`, `sum(gift_card_amount) = 205.00`, and order 9 `amount = 23.00`.

If the real upstream feed actually sends gift card amounts in dollars and you can't change it, normalize the unit in `stg_payments.sql` instead:

```sql
case when payment_method = 'gift_card' then amount else amount / 100 end as amount
```

The seed fix is cleaner, though, because it keeps one unit throughout the raw data.

Optionally, cast to a decimal in staging so totals print as `2.05` rather than `2.0500000000000003`. Floating-point noise is not something you want in finance reconciliations:

```sql
cast(amount / 100 as decimal(18, 2)) as amount
```

## 3. A test that would have caught it

Reconcile against the external control totals. Store finance's figures in a seed, `seeds/payment_control_totals.csv`:

```csv
payment_method,expected_total
all,1672.00
gift_card,205.00
```

Then add a singular test, `tests/assert_payments_reconcile_to_control_totals.sql`:

```sql
-- fails if modelled payment totals drift from processor / provider statements
with actuals as (

    select 'all' as payment_method, sum(amount) as actual_total
    from {{ ref('orders') }}

    union all

    select 'gift_card', sum(gift_card_amount)
    from {{ ref('orders') }}

)

select
    controls.payment_method,
    controls.expected_total,
    actuals.actual_total
from {{ ref('payment_control_totals') }} as controls
left join actuals
    on controls.payment_method = actuals.payment_method
where actuals.actual_total is null
   or abs(actuals.actual_total - controls.expected_total) > 0.01
```

Against the current data, this test returns both rows (1469.05 vs 1672.00, and 2.05 vs 205.00), so `dbt build` fails.

You can also add a cheap per-row guard on units, which needs no external data. Gift cards aren't issued in amounts under $1, so a gift card payment below 100 cents in the raw data means the unit is wrong. Put it in `tests/assert_gift_card_payments_in_cents.sql`:

```sql
select *
from {{ ref('raw_payments') }}
where payment_method = 'gift_card'
  and amount < 100
```

This test flags all 12 bad rows on the current data.
