claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 27 s | USD 0.176 | 2026-10-15 | project ad0b16b | prompt c4a4ba955988

## 1. Root cause

The ingestion job changed the shape of `raw_orders`. It added a new column, `order_status`, and stopped filling the old `status` column for new orders. The new column also uses a different word for completed orders: `delivered`.

From the outputs you posted:

- `raw_orders` now has both `status` and `order_status`. Older rows have both set (`completed | completed`). Newer rows have `status = NULL`, with `order_status` set to `placed`, `delivered`, and so on.
- In `orders`, 43 orders have `status = NULL`, totalling 761.00. That matches all of March and April exactly: March is 35 orders / 622.00 and April is 8 orders / 139.00, which together make 43 orders / 761.00. So every order since about 1 March lost its status.
- `jaffle_shop/models/staging/stg_orders.sql` still reads only `status`. For these rows `stg_orders.status` is NULL, and `customers.sql` (`case when orders.status = 'completed' ...`) counts them as not completed. The missing 279.00 is the March orders that the shop now calls `delivered`, and 824.00 + 279.00 = 1103.00. Amounts are unaffected, which is why `sum(orders.amount)` is still 1672.00.

**Why the build was green:** `accepted_values` ignores NULLs, and nothing tests `status` for `not_null`. The new values `delivered` and `placed` never reach the test, because the model doesn't read `order_status`.

## 2. Fix

First, confirm the full set of values in the new column and how they line up with the old one:

```sql
select r.status, r.order_status, count(*) as orders, sum(p.amount) / 100 as amount
from raw_orders r
left join raw_payments p on p.order_id = r.id
group by 1, 2 order by 1, 2;
```

You should see `delivered` adding up to 279.00, all in March. Then change `jaffle_shop/models/staging/stg_orders.sql` to prefer the new column and map it to the old vocabulary:

```sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,
        ordered_at,
        cast(ordered_at as date) as order_date,

        -- ingestion moved status to `order_status` (Mar 2018 onwards) and renamed completed -> delivered
        case coalesce(order_status, status)
            when 'delivered' then 'completed'
            else coalesce(order_status, status)
        end as status

    from source

)
```

If the query shows other new values (for example `in_transit` or `refunded`), add them to the `case`. The `accepted_values` test will now fail on any value you haven't mapped, which is what you want. After `dbt build`, `sum(completed_lifetime_value)` should be 1103.00 again.

## 3. Tests that would have caught it

Add these in `models/staging/schema.yml` (and the equivalent on `orders.status` in `models/schema.yml`):

```yaml
  - name: stg_orders
    columns:
      - name: status
        tests:
          - not_null          # accepted_values lets NULLs through; this is the gap
          - accepted_values:
              arguments:
                values: ['placed', 'shipped', 'completed', 'return_pending', 'returned']
```

`not_null` on `stg_orders.status` would have failed on this week's build with 43 rows.

For extra safety, add a check on the raw data that compares the old and new columns while both exist, at `tests/assert_order_status_consistent.sql`:

```sql
-- fails if the two status columns disagree, or a row has neither
select id, status, order_status
from {{ ref('raw_orders') }}
where coalesce(order_status, status) is null
   or (status is not null and order_status is not null
       and status <> case order_status when 'delivered' then 'completed' else order_status end)
```

Side note: `models/schema.yml` documents a `total_order_amount` column on `customers`, but the model outputs `customer_lifetime_value`. It's harmless, but worth fixing while you're in that file.
