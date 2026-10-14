claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 28 s | USD 0.071 | 2026-10-14 | project bef6b69

## 1. Root cause

The problem is in the raw data, and `models/staging/stg_payments.sql` hides it. That model does `amount / 100` on every row, assuming `raw_payments.amount` is always in cents.

Some coupon rows in `raw_payments` are already in whole dollars. From 2018-03-20 onward, the ingestion job loads them in dollars, so the blanket `/100` shrinks them 100x:

| order | raw `amount` | after `/100` | actual |
|---|---|---|---|
| 76, 81, 86 (March) | 2, 2, 23 | 0.02, 0.02, 0.23 | 2, 2, 23 |
| 92, 94, 95 (April) | 2, 7, 24 | 0.02, 0.07, 0.24 | 2, 7, 24 |

This explains every number you gave:
- March: 18 (order 58, correctly in cents) + 2 + 2 + 23 = **45.00**, against 18.27 in the model.
- April: 2 + 7 + 24 = **33.00**, against 0.33 in the model.
- January and February agree because those rows are still in cents.
- The March settlement gap is 622.00 − 595.27 = 26.73, which equals 45.00 − 18.27. Only the coupon rows are wrong, and the other payment methods are fine.

I can't see the seed data. The row values above are inferred from `coupon_amount` and the ledger totals, so confirm them with the query below.

Every existing test passes because none of them check amounts. `not_null` and `accepted_values` are satisfied by 0.02.

## 2. Fix

Fix the data at the source, and fix the ingestion job so it stops emitting dollars.

```sql
-- confirm first: should show the six small coupon rows, and none before 2018-03-20
select p.id, p.order_id, o.ordered_at, p.amount
from raw_payments p join raw_orders o on o.id = p.order_id
where p.payment_method = 'coupon' and o.ordered_at >= '2018-03-20'
order by p.id;

-- then correct those rows to cents
update raw_payments
set amount = amount * 100
where payment_method = 'coupon'
  and order_id in (76, 81, 86, 92, 94, 95);
```

Then run `dbt build`. The `orders` table will show March coupons at 45.00 and April at 33.00, and March `amount` will be 622.00.

`daily_revenue` is incremental and only rebuilds the last three days. The affected March days are already stale, so run `dbt run --full-refresh -s daily_revenue` as well.

Don't patch this in `stg_payments` with a per-row rule. Nothing in the data distinguishes dollars from cents, so a guess there would silently corrupt other rows.

## 3. Tests that would have caught it

The main one is a reconciliation against the ledger. This assumes you load the marketing ledger as a seed or source called `coupon_ledger(month, amount)`:

```sql
-- tests/assert_coupon_revenue_matches_ledger.sql
select
    l.month,
    l.amount as ledger_amount,
    coalesce(o.coupon_amount, 0) as model_amount
from {{ ref('coupon_ledger') }} l
left join (
    select date_trunc('month', order_date) as month, sum(coupon_amount) as coupon_amount
    from {{ ref('orders') }}
    group by 1
) o using (month)
where abs(l.amount - coalesce(o.coupon_amount, 0)) > 0.01
```

Add a drift check that works without an external source. It would have flagged the unit change as soon as it started:

```sql
-- tests/assert_payment_scale_stable.sql
with m as (
    select
        payment_method,
        date_trunc('month', o.order_date) as month,
        avg(p.amount) as avg_amount
    from {{ ref('stg_payments') }} p
    join {{ ref('stg_orders') }} o using (order_id)
    group by 1, 2
),
w as (
    select *, lag(avg_amount) over (partition by payment_method order by month) as prev_avg
    from m
)
select * from w
where prev_avg > 0 and (avg_amount / prev_avg < 0.1 or avg_amount / prev_avg > 10)
```

## Side finding

`region_revenue` joins `raw_customer_regions`, which has an `updated_at` column but no `unique` test on `customer_id`, despite the "one row per customer" description. If a customer has multiple rows, revenue is double-counted. Add `unique` on `customer_id`, or take the latest row per customer, and check that table.
