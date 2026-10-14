claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 30 s | USD 0.073 | 2026-10-14 | project bef6b69 | prompt aaf31b99f700

## 1. Root cause

**Not a model bug: eight orders in `raw_orders` lost their `ordered_at` timestamp during ingestion.** I can't see the seed data, so this is inferred from the outputs, but the numbers match exactly.

- `stg_orders` shows order IDs 66 to 73 with `ordered_at = NULL`. They sit between order 65 (Mar 8) and order 74 (Mar 17), so they are March orders.
- `orders` has a `NULL` month bucket with 8 orders and 149.00. March (473.00, 27 orders) plus that bucket gives 622.00 and 35 orders, which is the dashboard and shop admin figure.
- Customers 19 and 36 have one order each, and both orders have a NULL date. That is why their `first_order` and `most_recent_order` are NULL. Customers 3 and 79 have other dated orders, so they look fine.

**Why `daily_revenue` still shows 622.00:** it is incremental and only rebuilds `order_date >= max(order_date) - 3 days`. March was computed on an earlier run, when the timestamps were still present, and has been frozen since. So `daily_revenue` is stale but correct, while `orders` is fresh but wrong. The two can't be compared by eye, and nothing in the build compares them.

**Why every test passed:**
- Nothing checks `ordered_at` or `order_date` for nulls. `stg_orders` and `orders` only test the other columns.
- `orders.amount` and the other amount columns are not null, because payments exist for those orders.
- The `unique` and `not_null` tests on `daily_revenue.order_date` only see the frozen rows.

## 2. Fix

1. **Restore the data at the source.** Re-ingest `ordered_at` for orders 66 to 73 into `raw_orders` and find out why the job loaded NULLs. I can't invent the timestamps. `daily_revenue` shows which days had these orders but not which order belongs to which day.
2. **Rebuild `orders` and `customers`.** Run `dbt build -s orders customers`. `daily_revenue` is already correct, so it doesn't need to change.
3. **Don't run `--full-refresh` on `daily_revenue` before step 1.** It would rebuild March from the NULL data and wipe out the only correct copy. Back it up first (`create table daily_revenue_bak as select * from daily_revenue`). After step 1, a full refresh is safe and removes the stale-history risk.

## 3. Tests that would have caught it

Add a null check on the source column, in `models/staging/schema.yml` under `stg_orders`:

```yaml
      - name: ordered_at
        tests:
          - not_null
```

Also add one on `orders.order_date` in `models/schema.yml`. The build would have failed on the 8 NULL rows.

Add a reconciliation test that compares `orders` and `daily_revenue` per day, so staleness in the incremental model shows up too. Save it as `tests/daily_revenue_matches_orders.sql`:

```sql
with o as (
    select order_date, count(*) as orders, sum(amount) as revenue
    from {{ ref('orders') }}
    group by order_date
)

select coalesce(o.order_date, d.order_date) as order_date, o.orders, d.orders as dr_orders, o.revenue, d.revenue as dr_revenue
from o
full outer join {{ ref('daily_revenue') }} d on o.order_date = d.order_date
where o.orders is distinct from d.orders
   or round(o.revenue, 2) is distinct from round(d.revenue, 2)
```

A total-only comparison would not work here. The 149.00 sits in the NULL bucket in `orders` and in March in `daily_revenue`, so the totals match. The per-day comparison fails on the NULL bucket and on every March day that lost orders.

One unrelated thing I noticed: `raw_customer_regions` says "one row per customer" but has no `unique` test on `customer_id`. If it ever has duplicates, `region_revenue` will double-count revenue.
