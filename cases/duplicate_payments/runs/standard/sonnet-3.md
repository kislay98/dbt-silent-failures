claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 31 s | USD 0.128 | 2026-10-14 | project ad0b16b | prompt b041d9ee24db

## 1. Root cause

`seeds/raw_payments.csv` contains 12 re-delivered payments, ids 114-125. Each one repeats an earlier payment with the same order, method and amount, under a new `id`:

| dup id | original id | order | method | amount |
|---|---|---|---|---|
| 114 | 1 | 1 | credit_card | 10.00 |
| 115 | 11 | 10 | bank_transfer | 26.00 |
| 116 | 21 | 18 | credit_card | 8.00 |
| 117 | 31 | 26 | credit_card | 30.00 |
| 118 | 41 | 36 | bank_transfer | 9.00 |
| 119 | 51 | 46 | bank_transfer | 8.00 |
| 120 | 61 | 54 | credit_card | 18.00 |
| 121 | 71 | 62 | gift_card | 14.00 |
| 122 | 81 | 71 | credit_card | 5.00 |
| 123 | 91 | 79 | credit_card | 9.00 |
| 124 | 101 | 88 | credit_card | 29.00 |
| 125 | 111 | 97 | bank_transfer | 14.00 |

These sum to 180.00, which is exactly 1852 − 1672. `orders.sql` does `sum(payments.amount)` per order, so order 1 shows 20.00 instead of 10.00. The same inflation flows into `fees`, `customers.customer_lifetime_value` (1852.00) and `region_revenue`.

Every test passes because none of them check for this. `unique_stg_payments_payment_id` only looks at the surrogate id, which differs on the duplicates. Nothing compares totals to an external source.

`daily_revenue` is a second problem. It is incremental and only rebuilds the last 3 days (from 2018-04-06). Its Jan-Mar rows are stale from a build made before the duplicates landed, so they are 166.00 below `orders` (74 in Jan, 35 in Feb, 57 in Mar). The 14.00 duplicate on order 97 (Apr 7) is inside the window, so it is included. It looks right by accident, and the stale rows will not repair themselves if late data lands outside the window.

## 2. Fix

1. Delete lines 114-125 of `seeds/raw_payments.csv`. There are 113 real payments, and none of the first 113 rows share order, method and amount.
2. Run `dbt build --full-refresh`. The full refresh is needed so `daily_revenue` is rebuilt rather than patched.

Expected result: `sum(orders.amount)` is 1672.00, order 1 is 10.00, and `daily_revenue` totals 1672.00.

In a real pipeline the dedup belongs in the loader or in `stg_payments`. If you do it in `stg_payments`, use `qualify row_number() over (partition by order_id, payment_method, amount order by payment_id) = 1`. That is only safe if two identical split payments on one order can never be legitimate, so confirm that with finance first.

## 3. Tests that would have caught it

`tests/assert_no_duplicate_payments.sql`, which fails on the 12 pairs above:
```sql
select order_id, payment_method, amount, count(*) as n
from {{ ref('stg_payments') }}
group by 1, 2, 3
having count(*) > 1
```

`tests/assert_daily_revenue_matches_orders.sql`, which catches the stale incremental rows:
```sql
select
    (select sum(amount) from {{ ref('orders') }}) as orders_total,
    (select sum(revenue) from {{ ref('daily_revenue') }}) as daily_total
where abs(orders_total - daily_total) > 0.005
```

For finance's number, load the processor settlement as a seed (or source) and add a test that fails when `sum(orders.amount)` differs from it. That would have flagged the 180.00 gap on the first run.
