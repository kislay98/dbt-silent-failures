claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 29 s | USD 0.240 | 2026-10-14 | project ad0b16b | prompt b041d9ee24db

# Duplicated payment rows in `raw_payments.csv` inflate orders by exactly 180.00

## 1. Root cause

The last 12 rows of `jaffle_shop/seeds/raw_payments.csv` (ids **114–125**) duplicate earlier payments. Each one has the same `order_id`, `payment_method` and `amount` as an earlier row, but a new `id`:

| dup id | original id | order_id | method | amount (AUD) |
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

These rows total **180.00**, and 1852.00 − 180.00 = **1672.00**, which is the settlement total. Order 1 has two 10.00 credit card payments (ids 1 and 114), so it shows 20.00.

**Why every test passed:** the only uniqueness check on payments is `unique` on `payment_id`. The duplicates carry fresh ids, so that test passes. `models/orders.sql` then sums every payment per order in `order_payments`, so the doubled rows flow into `orders.amount`, the per-method columns and `fees`. They also flow into `customers` and `region_revenue`, which read `stg_payments` directly.

**A secondary problem: `daily_revenue` is stale.** Its total is 1686.00, which matches neither figure. It was built before the duplicates landed, and its incremental filter only rebuilds the last three days. The only duplicate inside that window is order 97 on 2018-04-07 (14.00), so 1672 + 14 = 1686. The monthly gaps between `daily_revenue` and `orders` (Jan 74, Feb 35, Mar 57) are exactly the duplicates in those months. This drift means the incremental model can silently disagree with `orders`, and no test checks for it.

## 2. Fix

Delete rows 114–125 from `seeds/raw_payments.csv`. Then reload the seed and fully rebuild everything downstream, because the incremental model won't correct older dates on its own:

```bash
dbt build --full-refresh
```

After the rebuild, `sum(orders.amount)` = 1672.00, order 1 = 10.00, and `daily_revenue` matches `orders` again.

I don't recommend deduplicating on `(order_id, payment_method, amount)` inside `stg_payments`. A customer can legitimately make two equal split payments on one order, and that rule would drop real money. If the upstream feed keeps replaying rows, fix it at ingestion, or dedupe on a real source-side key such as a processor transaction id.

## 3. Tests that would have caught this

**a) A duplicate-payment check.** This fails on the current data with 12 rows. Save it as `tests/assert_no_duplicate_payments.sql`:

```sql
-- A payment with the same order, method and amount as another payment is almost certainly a replayed row
select
    order_id,
    payment_method,
    amount,
    count(*) as payments
from {{ ref('stg_payments') }}
group by order_id, payment_method, amount
having count(*) > 1
```

If legitimate equal split payments do exist, set `config(severity='warn')` so the test flags them for review instead of failing the build.

**b) A reconciliation test.** This catches drift in the incremental model. Save it as `tests/assert_daily_revenue_matches_orders.sql`:

```sql
select
    o.order_date,
    o.revenue as orders_revenue,
    d.revenue as daily_revenue
from (
    select order_date, sum(amount) as revenue
    from {{ ref('orders') }}
    group by order_date
) o
full outer join {{ ref('daily_revenue') }} d
    on o.order_date = d.order_date
where coalesce(o.revenue, 0) <> coalesce(d.revenue, 0)
```

The strongest guard would be reconciling `sum(stg_payments.amount)` against the processor settlement total. That needs the settlement data loaded as a source or seed. If finance can provide it, it's worth adding.
