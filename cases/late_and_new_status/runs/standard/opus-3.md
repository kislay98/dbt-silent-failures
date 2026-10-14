claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 58 s | USD 0.239 | 2026-10-14 | project ad0b16b | prompt 2b2290cfef14

# Diagnosis: two separate problems behind the revenue mismatches

## 1. Root cause

There are two independent problems.

### A. Completed revenue is short by exactly the `delivered` orders

`raw_orders.status` now contains a new value, `delivered`. It covers 14 orders worth 201.00. The shop admin counts these as completed, but `models/customers.sql` only counts the literal `'completed'`:

```sql
sum(case when orders.status = 'completed' then amount else 0 end) as completed_amount
```

The numbers match exactly:

| | completed | + delivered | = admin |
|---|---|---|---|
| Since January | 967.00 | 201.00 | **1168.00** ✓ |
| March | 143.00 | 136.00 | **279.00** |

No other combination of statuses adds up to 201.00 (return_pending is 38, returned 49, shipped 198, placed 284).

The tests did not catch this because `'delivered'` was added to the `accepted_values` lists in both `models/schema.yml` and `models/staging/schema.yml`. That is the test that should have flagged the new value. It was widened, but `customers.sql` was never updated to match.

### B. `daily_revenue` is stale for February

`daily_revenue` is missing 6 orders worth 65.00, all in February. In total it has 99 orders against 105 in `orders`, and 1672.00 against 1737.00, so these orders are missing, not moved to another date.

`models/daily_revenue.sql` is incremental and only rebuilds `order_date >= max(order_date) - 3 days`. Rows for February orders that landed in `raw_orders` or `raw_payments` after February had already been built are never picked up. The raw tables have no `loaded_at` column, so the model has no way to notice late or backfilled data older than 3 days. `orders` is a full table rebuild, which is why it is correct.

I can't query the warehouse, so this next point is a guess, but the numbers line up: the delivered orders outside March also total exactly 65.00. That suggests the ingestion change that introduced `delivered` also backfilled about 6 February orders. This query checks it:

```sql
select o.order_date, o.status, count(*) n, sum(o.amount) amt,
       any_value(d.orders) dr_orders, any_value(d.revenue) dr_revenue
from orders o left join daily_revenue d using (order_date)
where o.order_date between '2018-02-01' and '2018-02-28'
group by all
having count(*) over (partition by o.order_date) is not null
order by 1;
-- simpler check: dates where daily_revenue disagrees with orders
select order_date, count(*) n, sum(amount) from orders group by 1
except
select order_date, orders, revenue from daily_revenue;
```

## 2. Fix

### A. Classify statuses explicitly

First confirm with ops that `delivered` means completed in the admin. The numbers say it does.

Then add a seed, `seeds/order_statuses.csv`:

```csv
status,is_completed
placed,false
shipped,false
completed,true
delivered,true
return_pending,false
returned,false
```

In `models/staging/stg_orders.sql`, join the seed and expose the flag:

```sql
renamed as (

    select
        source.id as order_id,
        source.user_id as customer_id,
        source.ordered_at,
        cast(source.ordered_at as date) as order_date,
        source.status,
        order_statuses.is_completed

    from source

    left join {{ ref('order_statuses') }} as order_statuses
        on source.status = order_statuses.status

)
```

In `models/customers.sql`, use the flag instead of the literal status:

```sql
sum(case when orders.is_completed then amount else 0 end) as completed_amount
```

Also update the `orders_status` doc block to describe `delivered`.

### B. Rebuild `daily_revenue` and stop it drifting again

Rebuild it once now:

```bash
dbt build --select daily_revenue --full-refresh
```

For the long term, `orders` has 105 rows, so incremental buys nothing here. Set `materialized='table'` in `daily_revenue.sql` and remove the `is_incremental()` block.

If it has to stay incremental, there are two options:
- Ask ingestion to add a `loaded_at` column, and filter on `order_date in (select order_date from orders where loaded_at > max loaded_at in {{ this }})`.
- Schedule a regular `--full-refresh`.

A wider lookback window only shrinks the problem; it doesn't remove it.

## 3. Tests that would have caught this

**Every status must be explicitly classified.** A new value then fails the build until someone decides whether it counts as completed. Widening `accepted_values` is no longer the only step needed.

```yaml
# models/staging/schema.yml, under stg_orders
      - name: status
        tests:
          - relationships:
              arguments:
                to: ref('order_statuses')
                field: status
      - name: is_completed
        tests:
          - not_null
```

```yaml
# seeds
  - name: order_statuses
    columns:
      - name: status
        tests: [unique, not_null]
      - name: is_completed
        tests: [not_null]
```

**`daily_revenue` must reconcile with `orders`.** Save this as `tests/assert_daily_revenue_matches_orders.sql`:

```sql
with expected as (
    select order_date, count(*) as orders, sum(amount) as revenue
    from {{ ref('orders') }}
    group by 1
),

actual as (
    select order_date, orders, revenue
    from {{ ref('daily_revenue') }}
)

select
    coalesce(expected.order_date, actual.order_date) as order_date,
    expected.orders as expected_orders,
    actual.orders as actual_orders,
    expected.revenue as expected_revenue,
    actual.revenue as actual_revenue
from expected
full outer join actual on expected.order_date = actual.order_date
where expected.orders is distinct from actual.orders
   or abs(coalesce(expected.revenue, 0) - coalesce(actual.revenue, 0)) > 0.005
```

On the current data this test returns the February dates and fails the build. The existing `unique` and `not_null` tests on `daily_revenue` could never catch missing rows or wrong totals.
