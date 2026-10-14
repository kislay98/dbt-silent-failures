claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 43 s | USD 0.008 | 2026-10-14 | project ad0b16b | prompt 150d20052204

## 1. Root cause

**`jaffle_shop/models/daily_revenue.sql`**, in the incremental filter:

```sql
{% if is_incremental() %}
-- payments can land a day or two after the order, so the last three days are rebuilt on every run
where order_date >= (select max(order_date) - interval 3 day from {{ this }})
{% endif %}
```

`daily_revenue` is incremental with `delete+insert` on `order_date`, so each run only rewrites dates within three days of the latest date already in the table. The backfilled orders are dated in January and February, months before that window, so no run ever rewrites those days. `orders` is a full `table` rebuild, so it picks them up immediately, and the two tables diverge.

The comment gives the wrong reason for the window. Payments are already joined inside `orders`, which is rebuilt in full, so the window does nothing for payment lag. It only drops late-arriving history.

The numbers match this. Comparing `orders` with `daily_revenue` by month:

| Month | orders | daily_revenue | revenue diff |
|---|---|---|---|
| Jan | 30 / 502.00 | 29 / 496.00 | −1 order, −6.00 |
| Feb | 32 / 474.00 | 27 / 415.00 | −5 orders, −59.00 |

Total: 6 orders and 65.00 missing, which is the backfill ops described.

Two things do not line up and need checking against the data:

- **One of the six is in January.** `orders` has it dated January, not February. Its `ordered_at` is probably late on 31 January UTC. Per the schema, `order_date` is the UTC date (`cast(ordered_at as date)`), and eastern Australian time was UTC+11 in February 2018. A UTC timestamp after about 13:00 on 31 January is therefore 1 February locally, which would explain why the shop admin shows it on 1 Feb. I have not confirmed this.
- **The first backfilled order is not a timezone case.** 2018-02-01 04:48:19 UTC is 15:48 on 1 February in AEDT, so it has the same date in both zones.

To confirm which rows are missing, run this against the warehouse:

```sql
select o.order_date, o.orders, d.orders as dr_orders, o.revenue, d.revenue as dr_revenue
from (select order_date, count(*) as orders, sum(amount) as revenue from orders group by 1) o
full outer join daily_revenue d using (order_date)
where o.orders is distinct from d.orders or o.revenue is distinct from d.revenue
order by 1;
```

I have not run this. The diagnosis comes from the output you pasted.

## 2. Fix

**Make `daily_revenue` a full rebuild.** It has about 69 rows and is derived from `orders`, which is already a full rebuild, so the incremental logic gives no benefit and causes this bug.

`jaffle_shop/models/daily_revenue.sql`:

```sql
{{
    config(
        materialized='table'
    )
}}

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

Then update the description in `jaffle_shop/models/schema.yml`, which currently says "Incremental, rebuilds the last three days on each run":

```yaml
  - name: daily_revenue
    description: Orders and revenue per order date. Rebuilt in full on each run
```

Run `dbt build --select daily_revenue --full-refresh` once to correct the existing February and January rows.

**Timezone (decide with ops, do not change silently):** `order_date` is UTC, and the shop admin reports local dates. That mismatch will keep producing reconciliation differences for orders placed after about 13:00 UTC, even after this fix. Changing `order_date` to a local date would change `customers.first_order`, `most_recent_order`, and every downstream dashboard, so it should be a deliberate change. If you do make it, convert explicitly, for example `cast(ordered_at at time zone 'UTC' at time zone 'Australia/Sydney' as date)`, and update the descriptions.

## 3. Test that would have caught it

The existing tests only check `not_null` and `unique` on `daily_revenue`, so the build passed while the two tables disagreed. Add a reconciliation test over the full history, not just the incremental window. I'd put it at `jaffle_shop/tests/assert_daily_revenue_matches_orders.sql`:

```sql
-- Every order date must have the same order count and revenue in daily_revenue as in orders.
-- Returns the mismatched dates; the test fails if any are returned.
with expected as (

    select
        order_date,
        count(*) as orders,
        sum(amount) as revenue

    from {{ ref('orders') }}

    group by order_date

),

actual as (

    select * from {{ ref('daily_revenue') }}

)

select
    coalesce(expected.order_date, actual.order_date) as order_date,
    expected.orders as expected_orders,
    actual.orders as actual_orders,
    expected.revenue as expected_revenue,
    actual.revenue as actual_revenue

from expected
full outer join actual
    on expected.order_date = actual.order_date

where expected.orders is distinct from actual.orders
   or expected.revenue is distinct from actual.revenue
```

On the current run this returns the January and February rows, so the build would have failed instead of passing. Running it on every build means any future divergence between a derived table and its source fails the run, not only changes inside a three-day window.
