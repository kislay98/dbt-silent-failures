claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 42 s | USD 0.154 | 2026-10-14 | project f769fdf | prompt 8e741d6ac5d0

# Root cause

**The bad data is in `raw_orders`. Eight March orders, ids 66–73, have `ordered_at = NULL`.** Your source for that table may be the ingestion job or `seeds/raw_orders.csv`. The SQL in the models is correct. The numbers account for the whole gap:

| | orders | amount |
|---|---|---|
| `orders`, March | 27 | 473.00 |
| `orders`, `NULL` month | 8 | 149.00 |
| **Sum** | **35** | **622.00**, which matches the dashboard and shop admin |

The damage spreads like this:

- **`stg_orders.sql`**: `cast(ordered_at as date)` returns `NULL` for those rows. That is why orders 66–73 show `NULL` in the sample.
- **`orders.sql`**: These rows land in a `NULL` month, so March is short by 8 orders and 149.00.
- **`customers.sql`**: `min(order_date)` ignores `NULL`s. Customers 19 and 36 have only one order each, and it is one of the affected orders. So their `first_order` and `most_recent_order` are `NULL`, while `number_of_orders = 1`. Customers 3 and 79 also have an affected order, but it is hidden behind their earlier orders. Their `most_recent_order` is now wrong, though: customer 3 shows 2018-01-27 when their latest order was in March.
- **`daily_revenue.sql`** is correct only by accident. It is incremental with `delete+insert`, and each run rebuilds only `order_date >= max(order_date) - 3 days`, which today means April. Its March rows were built before the timestamps were lost, and nothing has reprocessed them since. The affected dates add up to the missing amount: 03-10, 03-11 (×3), 03-12 (×2), 03-14 and 03-16 give 8 orders and 28+58+31+29+3 = 149.00.

**Why every test passed:** Nothing tests `ordered_at` or `order_date` for nulls. The incremental filter (`order_date >= …`) also drops `NULL` dates, so `daily_revenue` never gets a `NULL` row and `not_null_daily_revenue_order_date` stays green.

I can't see the raw data, so I can't tell why the loader nulled those values. A common cause is a different timestamp format or timezone suffix in that batch, which a lenient or `try_cast` loader turns into `NULL`. Check the ingestion job's log for that load.

# Fix

1. **Repair the data, not the models.** Restore `ordered_at` for ids 66–73 from the shop admin, then re-run ingestion or patch `raw_orders`. You can't rebuild the timestamps from `daily_revenue`: it has only dates and counts (03-11 holds 3 orders), not per-order values.
2. Run `dbt build`. `stg_*` are views and `orders`/`customers` are tables, so they will be correct right away.
3. **Do not run `dbt build --full-refresh -s daily_revenue` before step 1.** Right now that table is the only correct copy of March in the warehouse. A full refresh would replace it with 473.00 plus a `NULL`-date row. After the data is fixed, a full refresh is a good way to confirm everything agrees.

# Tests that would have caught it

Add these to `models/staging/schema.yml` under `stg_orders`:

```yaml
      - name: ordered_at
        tests:
          - not_null
      - name: order_date
        tests:
          - not_null
```

Add this to `models/schema.yml` under `orders`:

```yaml
      - name: order_date
        description: Date (UTC) that the order was placed, from the ordered_at timestamp
        tests:
          - not_null
```

Add a reconciliation test, because a stale incremental model can drift from its source and no column test will notice. Put this in `tests/assert_daily_revenue_matches_orders.sql`:

```sql
-- daily_revenue is incremental and only rebuilds recent days, so check it still agrees with orders
with orders_by_day as (

    select
        order_date,
        count(*) as orders,
        sum(amount) as revenue

    from {{ ref('orders') }}

    group by order_date

)

select
    coalesce(o.order_date, d.order_date) as order_date,
    o.orders as orders_from_orders,
    d.orders as orders_from_daily_revenue,
    o.revenue as revenue_from_orders,
    d.revenue as revenue_from_daily_revenue

from orders_by_day as o

full outer join {{ ref('daily_revenue') }} as d
    on o.order_date = d.order_date

where o.order_date is null
   or d.order_date is null
   or o.orders != d.orders
   or abs(coalesce(o.revenue, 0) - coalesce(d.revenue, 0)) > 0.005
```

On the current data this returns the `NULL`-date group and the five March dates. It would also catch the reverse case: a raw-data correction that the incremental window never picks up.

# Unrelated issues I noticed

- The log says `Found … 4 seeds, 29 data tests`, but only 27 tests ran and no seeds ran. The two tests on `raw_customer_regions` are not running in this build.
- `raw_customer_regions` has an `updated_at` column, which suggests it may hold more than one row per customer. It has no `unique` test on `customer_id`. If a customer has duplicate rows, `region_revenue` will double-count that customer's revenue.
