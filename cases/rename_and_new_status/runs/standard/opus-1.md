claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 25 s | USD 0.173 | 2026-10-15 | project ad0b16b | prompt c4a4ba955988

# Root cause: `raw_orders` changed shape, and `stg_orders.sql` still reads the old `status` column

The ingestion job now loads a new column, `raw_orders.order_status`. For recent orders it fills only `order_status` and leaves the old `status` column NULL. `jaffle_shop/models/staging/stg_orders.sql` still selects `status`, so those orders have no status at all.

The data shows this in several places:

- **Status counts:** `orders` by status gives 50 completed (824.00), 2 return_pending, 4 returned, and **43 NULL (761.00)**. The 43 NULLs equal the March (35) plus April (8) order counts. Every order from March onward lost its status.
- **Raw sample:** older rows have both columns set (`status = completed`, `order_status = completed`). Newer rows have `status = NULL`, with `order_status` values `placed` and `delivered`. `delivered` is not a value the project knows about.
- **The gap:** 1103.00 − 824.00 = 279.00, exactly finance's March completed revenue. Those orders are now NULL-status (shown as `delivered` in `order_status`), so `customers.sql` drops them at `case when orders.status = 'completed'`.
- **Totals unaffected:** `sum(orders.amount)` (1672), `daily_revenue` and `region_revenue` don't filter on status, so they still look right. That made this hard to spot.

**Why every test passed:** `accepted_values` on `stg_orders.status` and `orders.status` doesn't flag NULLs, since `NULL not in (...)` is not true. Neither model has a `not_null` test on `status`. Nothing checks the raw `order_status` column either.

# Fix

Read the new column in `stg_orders.sql` and map its vocabulary onto the existing statuses. First, confirm the mapping. Run this and check that the `delivered` rows in March sum to 279.00 in the payments:

```sql
select o.status, o.order_status, date_trunc('month', o.ordered_at) as month,
       count(*) as orders, sum(p.amount) / 100 as amount
from raw_orders o left join raw_payments p on p.order_id = o.id
group by all order by all;
```

Then update `jaffle_shop/models/staging/stg_orders.sql`:

```sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,
        ordered_at,
        cast(ordered_at as date) as order_date,

        -- the orders feed moved from `status` to `order_status`, which reports completed orders as `delivered`
        case coalesce(order_status, status)
            when 'delivered' then 'completed'
            else coalesce(order_status, status)
        end as status

    from source

)
```

If the query shows other new values (for example a new name for returns), add them to the `case`. Finance should also confirm with the ingestion team whether `delivered` means `completed` or is a separate stage. After a rebuild, `sum(completed_lifetime_value)` should be 1103.00.

# Tests that would have caught this

Add these to `jaffle_shop/models/staging/schema.yml` (and the same `not_null` on `orders.status` in `models/schema.yml`):

```yaml
  - name: stg_orders
    columns:
      - name: status
        tests:
          - not_null          # the actual gap: accepted_values ignores NULLs
          - accepted_values:
              arguments:
                values: ['placed', 'shipped', 'completed', 'return_pending', 'returned']
```

`not_null` alone would have failed this build with 43 rows.

Two further guards are worth adding:

- **Declare `raw_orders` as a source** with `not_null` and `accepted_values` tests on `order_status`. Then a new value like `delivered`, or a new column, fails at the edge instead of propagating downstream.
- **Add a singular reconciliation test**, e.g. `tests/assert_all_paid_orders_have_status.sql`:

  ```sql
  select order_id from {{ ref('orders') }} where status is null and amount > 0
  ```

  This guards revenue specifically, even if someone later relaxes the column tests.
