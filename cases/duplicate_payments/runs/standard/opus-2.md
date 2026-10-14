claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 34 s | USD 0.249 | 2026-10-14 | project ad0b16b | prompt b041d9ee24db

# Root cause: 12 duplicated payment rows in `raw_payments.csv`

## 1. Root cause

`jaffle_shop/seeds/raw_payments.csv`, **rows `id` 114–125**, repeat earlier payments. Each one has a new `id` but the same `order_id`, `payment_method` and `amount` as an existing payment:

| dup id | copy of id | order_id | method | amount (¢) |
|---|---|---|---|---|
| 114 | 1 | 1 | credit_card | 1000 |
| 115 | 11 | 10 | bank_transfer | 2600 |
| 116 | 21 | 18 | credit_card | 800 |
| 117 | 31 | 26 | credit_card | 3000 |
| 118 | 41 | 36 | bank_transfer | 900 |
| 119 | 51 | 46 | bank_transfer | 800 |
| 120 | 61 | 54 | credit_card | 1800 |
| 121 | 71 | 62 | gift_card | 1400 |
| 122 | 81 | 71 | credit_card | 500 |
| 123 | 91 | 79 | credit_card | 900 |
| 124 | 101 | 88 | credit_card | 2900 |
| 125 | 111 | 97 | bank_transfer | 1400 |

The duplicates add up to $10+26+8+30+9+8+18+14+5+9+29+14 = **$180.00**. That is exactly the gap: 1852.00 − 180.00 = **1672.00**, the settlement total. Order 1 has payments 1 and 114, each for $10, so it shows $20.

**Why every test passed:** `unique_stg_payments_payment_id` checks only the surrogate `id`, and the duplicates have fresh ids. Nothing checks the business key or reconciles totals. The inflation flows into `orders.amount` and `fees`, `customers.customer_lifetime_value` (1852), and `region_revenue`, which also sums to 1852.

**Corroborating evidence in `daily_revenue`:** it totals 1686, not 1852. Its per-month gaps against `orders` match the duplicates exactly:
- Jan: −74 (orders 1, 10, 18, 26)
- Feb: −35 (orders 36, 46, 54)
- Mar: −57 (orders 62, 71, 79, 88)

So the incremental table was built before the duplicates landed. Only the 3-day lookback window (≥ 2018-04-06) was rebuilt afterwards, which pulled in duplicate 125 (order 97, 2018-04-07, $14): 1672 + 14 = 1686. The duplicates were appended after the original load, which points to a re-delivered or replayed batch in the feed.

## 2. Fix

**Data fix (primary):** delete rows `id` 114–125 from `seeds/raw_payments.csv`. Then rebuild, including a full refresh of the incremental model so no stale state survives:

```bash
dbt build --full-refresh
```

`sum(orders.amount)` will then be 1672.00, and order 1 will show 10.00.

I'd avoid adding a blanket `distinct` or dedupe on `(order_id, payment_method, amount)` in `stg_payments`. Legitimate split payments exist: order 18 has two credit-card payments (500 and 800), as do orders 49, 51 and 79. A future genuine same-amount split would be silently dropped. Confirm with whoever owns the payments feed why the batch was replayed, and fix it there.

## 3. Tests that would have caught it

**a) Duplicate-payment guard** (`tests/assert_no_duplicate_payments.sql`). Severity is `warn` because a real same-amount split is possible but rare enough to be worth a human look:

```sql
{{ config(severity='warn') }}

-- Payments that repeat another payment's order, method and amount under a new id
select
    order_id,
    payment_method,
    amount,
    count(*) as payments
from {{ ref('stg_payments') }}
group by order_id, payment_method, amount
having count(*) > 1
```

On the current data this returns 12 rows.

**b) Cross-model reconciliation** (`tests/assert_daily_revenue_matches_orders.sql`). This would have **failed** here (1852 vs 1686). It also catches drift in the incremental model in general:

```sql
-- daily_revenue must reconcile with orders
with o as (select coalesce(sum(amount), 0) as total from {{ ref('orders') }}),
     d as (select coalesce(sum(revenue), 0) as total from {{ ref('daily_revenue') }})
select o.total as orders_total, d.total as daily_revenue_total
from o, d
where abs(o.total - d.total) > 0.005
```

Finally, if finance can export the processor settlement as a seed or source, a singular test that checks `sum(stg_payments.amount)` against it is the strongest guard. It directly encodes the check finance did by hand.

A side note on `daily_revenue`: a 3-day lookback can't pick up corrections to older orders. After any backfill or data fix, it needs `--full-refresh`, or a wider lookback.
