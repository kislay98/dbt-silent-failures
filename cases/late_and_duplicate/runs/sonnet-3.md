claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 30 s | USD 0.076 | 2026-10-14 | project 7d5b525+dirty

I couldn't see the seed CSVs, so the duplicate-payment part is inferred from the numbers. The query in section 2 will confirm it.

## 1. Root cause

There are two independent defects, and every test passes because none of them checks anything that is actually wrong.

**A. `seeds/raw_payments.csv` (surfaced through `stg_payments.sql`) holds duplicate payments.**
- `orders.amount` (1917.00) equals `sum(stg_payments.amount)` (1917.00). `orders` is therefore faithfully summing whatever is in `raw_payments`, and the settlement is 1737.00, so about 180.00 of payments are phantom.
- 131 payment rows cover 105 orders, and the backfill most likely re-loaded payments for orders that already had them.
- Order 1 shows 20.00 against 10.00 charged, which is the same payment twice.
- The re-loaded rows have fresh `id`s, so `unique_stg_payments_payment_id` passes. Nothing tests uniqueness at the grain that matters, which is the real-world payment (order, method, amount).

**B. `models/daily_revenue.sql` is a frozen incremental that can't absorb a backfill.**
- The incremental filter is `order_date >= max(order_date) - 3 days`. The latest data is in April, so anything older is never rebuilt.
- The backfilled February orders therefore never reached the dashboard.
  - 2018-02-03 and 2018-02-22 are missing entirely.
  - Several other February days have fewer orders than `orders`.
  - Overall, `daily_revenue` has 99 orders against 105.
- The Jan and Mar rows have matching order counts but lower amounts. They are stale snapshots from before the duplicate payments landed. That is why the dashboard (1686) is also not 1737.
- The comment says "payments can land a day or two after the order", but the window is anchored on the newest order date, not on load time. Any late or backfilled data older than three days is silently lost.

Together, `orders` is about 180.00 too high from the duplicates, and the dashboard is stale, partly short on orders and partly stale on amounts.

## 2. Fix

**Data.** Find the duplicates first:

```sql
select order_id, payment_method, amount, count(*) as n, list(id order by id) as ids
from raw_payments
group by 1, 2, 3
having count(*) > 1
order by order_id;
```

Remove the later-id copies from the backfilled load, ideally at the source or loader, since the loader should be idempotent. Check that `sum(amount)/100` over the remaining rows is exactly 1737.00. If an order legitimately has two identical payments, the processor settlement will tell you which copies to keep. Don't blindly dedupe in SQL on those three columns.

**`daily_revenue.sql`.** This is a cheap aggregate over about 100 rows, so drop the incremental logic:

```sql
{{ config(materialized='table') }}

with orders as (

    select * from {{ ref('orders') }}

),

final as (

    select
        order_date,
        count(*) as orders,
        sum(amount) as revenue

    from orders

    group by order_date

)

select * from final
```

If it must stay incremental later, key the window on load time (`_loaded_at`), not on `max(order_date)`. For this run, `dbt build --full-refresh -s daily_revenue+` rebuilds it either way.

## 3. Tests that would have caught it

**Duplicate payments at the real grain.** Add `tests/assert_no_duplicate_payments.sql`:

```sql
select order_id, payment_method, amount, count(*) as n
from {{ ref('stg_payments') }}
group by 1, 2, 3
having count(*) > 1
```

Set it to `severity: warn` if legitimate repeat payments exist.

**The dashboard must tie out to `orders`.** Add `tests/assert_daily_revenue_matches_orders.sql`. It is essentially your diagnostic query, and it fails on both the missing days and the stale amounts:

```sql
select o.order_date
from (
    select order_date, count(*) as n, sum(amount) as amt
    from {{ ref('orders') }}
    group by 1
) o
left join {{ ref('daily_revenue') }} d using (order_date)
where o.n <> coalesce(d.orders, 0)
   or abs(o.amt - coalesce(d.revenue, 0)) > 0.005
```

**Reconcile to the processor.** Load finance's settlement figure as a seed or `var`, for example `seeds/processor_settlement.csv`, and add a test that fails if `sum(orders.amount)` differs from it. That is the only check that would have caught the 180.00 of duplicates directly.

## Side note

`region_revenue` inner-joins `raw_customer_regions`, which has an `updated_at` column. That suggests a history feed with possibly several rows per customer, and the seed only tests `not_null`. I didn't see evidence of a problem in your outputs, but add `unique` on `customer_id` there, since duplicates would silently multiply revenue.
