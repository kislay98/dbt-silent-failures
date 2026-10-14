claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 20 s | USD 0.087 | 2026-10-15 | project ad0b16b | prompt c4a4ba955988

## 1. Root cause

The ingestion job added a new `order_status` column to `raw_orders` and stopped populating the old `status` column for newer orders. `models/staging/stg_orders.sql` still reads only `status`:

```sql
status
```

In the raw table, orders from about March on have `status = NULL` and the real value only in `order_status` (e.g. order 84 `placed`, orders 73 and 67 `delivered`). The new column also uses a different vocabulary: `delivered` is not in the accepted list.

The effects are:

- `orders.status` is NULL for 43 orders (761.00 of amount), which are the recent ones.
- `customers.completed_lifetime_value` uses `case when orders.status = 'completed'`, so NULL-status orders count as 0. That leaves 824.00, the 50 orders that still have a legacy `status`.
- 1103.00 − 824.00 = 279.00, which matches the March amount finance sees in the shop admin. Those are the March orders whose completion is only recorded in `order_status`.
- `sum(orders.amount)` is unaffected, because amount doesn't depend on status.

Every test passes because none of them can fail on NULL:

- `accepted_values` ignores NULLs.
- There is no `not_null` test on `status` in `stg_orders` or `orders`.
- The staging layer silently dropped the new column, so nothing compares it to the old one.

The other models (`daily_revenue`, `region_revenue`, fees, `raw_payment_methods` and `raw_customer_regions` joins) don't depend on status and look consistent.

## 2. Fix

In `stg_orders.sql`, take the status from whichever column is populated and map the new vocabulary onto the old one:

```sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,
        ordered_at,
        cast(ordered_at as date) as order_date,
        coalesce(
            status,
            case order_status
                when 'delivered' then 'completed'
                else order_status
            end
        ) as status

    from source

)
```

Before shipping, confirm the `delivered` → `completed` mapping. I inferred it from the numbers, not from anything documented, so ask the ingestion owner or check the shop admin. Run this against the raw table and check that the sum is 279.00 for March and 1103.00 overall:

```sql
select date_trunc('month', o.ordered_at) m, sum(p.amount/100.0)
from raw_orders o join raw_payments p on p.order_id = o.id
where coalesce(o.status, o.order_status) in ('completed','delivered')
group by 1;
```

If the totals don't match, adjust the mapping (e.g. `delivered` may be the same as `shipped`).

Then run `dbt build`. `customers` and `orders` are full-refresh tables, so they correct themselves. `daily_revenue` doesn't use status, so it needs no backfill.

## 3. Tests that would have caught it

1. Add `not_null` to `status` in `stg_orders` and `orders` in the two `schema.yml` files. This alone would have failed on the 43 rows:

   ```yaml
   - name: status
     tests:
       - not_null
       - accepted_values: ...
   ```

2. Add a singular test, `tests/assert_completed_revenue_reconciles.sql`, that reconciles the final output to the raw source independent of the staging logic:

   ```sql
   with raw as (
       select sum(p.amount) / 100.0 as amt
       from {{ ref('raw_orders') }} o
       join {{ ref('raw_payments') }} p on p.order_id = o.id
       where coalesce(o.status, o.order_status) in ('completed', 'delivered')
   ),
   mart as (
       select sum(completed_lifetime_value) as amt from {{ ref('customers') }}
   )
   select * from raw, mart where abs(raw.amt - mart.amt) > 0.01
   ```

3. Add an accepted_values test on `raw_orders.order_status`, and a `not_null` test on `coalesce(status, order_status)`. These flag new vocabulary at the source (like `delivered`) instead of letting it slip through.
