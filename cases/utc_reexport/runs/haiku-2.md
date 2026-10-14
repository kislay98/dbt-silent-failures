claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 54 s | USD 0.008 | 2026-10-14 | project bef6b69 | prompt f253c60e27c9

## 1. Root cause

**`jaffle_shop/models/staging/stg_orders.sql`** derives the order day in UTC:

```sql
ordered_at,
cast(ordered_at as date) as order_date,
```

`ordered_at` is a UTC timestamp, so the cast gives the UTC calendar day. The admin screen buckets by India local time (Asia/Kolkata, UTC+5:30). Any order placed between 18:30 and 23:59:59 UTC falls on the next IST day. Order 73 is the example: `2018-03-15 19:45:20` UTC is `2018-03-16 01:15:20` IST, which matches the admin screen.

The row-level shifts in the ops list follow this pattern. Admin January 17 shows 3 where UTC shows 4, and admin January 18 shows 2 where UTC shows 1. Monthly totals agree because almost all orders stay in the same calendar month.

The incremental model makes this worse. **`jaffle_shop/models/daily_revenue.sql`** only rebuilds `order_date >= max(order_date) - 3 days`. Rows older than that are never recomputed. The dashboard therefore still holds the local-day buckets from before the data was corrected, which is why it matches the admin screen for January to March. The dashboard has five dates with no UTC orders at all, and those are the admin-only days (January 7, 9, 11, 12 and 15), which fits this explanation. The April 3 mismatch sits at the edge of the rebuild window, so I can't tell from the sample alone whether it is stale or caused by the window. A full refresh resolves both.

Nothing in the project is wrong in a way the tests could see. The tests check uniqueness, nulls and accepted values. None of them check the value of `order_date`, and none compare `daily_revenue` with `orders`.

I worked from the samples you gave me and did not run any queries. To confirm, run the fixed expression below against `raw_orders` for id 73 and expect `2018-03-16`.

## 2. Fix

I'm treating the business day as the IST day, since ops and the admin screen both use it. The alternative is to add a separate `order_date_ist` column and point `daily_revenue` at it. That keeps `order_date` unchanged for anything that depends on UTC, but the `orders` table would still disagree with the admin screen, so I don't recommend it.

**`jaffle_shop/models/staging/stg_orders.sql`:**

```sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,
        ordered_at,
        -- ordered_at is stored in UTC; the business day is India local time
        cast((ordered_at at time zone 'UTC') at time zone 'Asia/Kolkata' as date) as order_date,
        status

    from source

)
```

**`jaffle_shop/models/schema.yml`:** update the descriptions that say UTC.

- `orders.order_date`: "Date (IST, Asia/Kolkata) that the order was placed, from the ordered_at timestamp (stored in UTC)"
- `customers.first_order` and `most_recent_order`: change "(UTC)" to "(IST)". These change too, since they are derived from `order_date`.

**Rebuild the dashboard from scratch.** The incremental model will not repair history on its own:

```
dbt build --select stg_orders+ --full-refresh
```

Afterwards, check that order 73 is on 2018-03-16 and that the ops dates now match.

**Related risk in `daily_revenue.sql`.** The comment says payments can land a day or two late, but the filter is on `order_date`, not on payment time. A payment that arrives later than three days after its order will never reach the dashboard. The table is small, so I would make it `materialized='table'` and drop the incremental logic. If volume is large, keep it incremental and widen the window instead.

## 3. Tests that would have caught this

**a) A unit test pinning the timezone rule.** This runs on every build and is cheap. Add it to `jaffle_shop/models/staging/schema.yml`:

```yaml
unit_tests:
  - name: order_date_is_ist_day
    model: stg_orders
    given:
      - input: ref('raw_orders')
        rows:
          - {id: 73, user_id: 19, ordered_at: '2018-03-15 19:45:20', status: completed}
          - {id: 1, user_id: 1, ordered_at: '2018-01-01 11:38:01', status: returned}
    expect:
      rows:
        - {order_id: 73, order_date: '2018-03-16'}
        - {order_id: 1, order_date: '2018-01-01'}
```

**b) A reconciliation test between the two tables.** This would have failed on the first run, and it is the test that catches stale history. Create `jaffle_shop/tests/assert_daily_revenue_matches_orders.sql`:

```sql
with expected as (

    select order_date, count(*) as orders, round(sum(amount), 2) as revenue
    from {{ ref('orders') }}
    group by 1

),

actual as (

    select order_date, orders, round(revenue, 2) as revenue
    from {{ ref('daily_revenue') }}

)

select
    order_date,
    expected.orders as expected_orders,
    actual.orders as actual_orders,
    expected.revenue as expected_revenue,
    actual.revenue as actual_revenue

from expected
full outer join actual using (order_date)
where expected.orders is distinct from actual.orders
   or expected.revenue is distinct from actual.revenue
```

A singular test passes when it returns zero rows, so this fails on any date where the two tables disagree.

**Separate, while you're in there:** `raw_customer_regions` has only a `not_null` test on `customer_id`, but the seed description says "one row per customer". If the feed ever has duplicates, `region_revenue` will double-count revenue, because `sum(payments.amount)` is computed after the regions join. Add a `unique` test on `customer_id` in the seed's schema.
