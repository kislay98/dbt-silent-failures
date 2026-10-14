claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 22 s | USD 0.064 | 2026-10-14 | project f769fdf+wk6ext | prompt 56722c109409

## 1. Root cause

`models/staging/stg_orders.sql` reads only `status` from `raw_orders`. Starting with order 57 (2018-03-01), the ingestion job stopped filling `status` and writes the value to a new column, `order_status`, instead. That column is visible in the raw schema and in the sample rows (ids 57 onward have `status = NULL`).

- 43 orders (35 in March, 8 in April) therefore get `status = NULL` in `stg_orders` and `orders`.
- In `customers.sql`, `case when orders.status = 'completed'` is never true for them, so their payments drop out of `completed_lifetime_value`. That accounts for 1103 − 824 = 279, which is the March completed amount from the shop admin.
- `sum(orders.amount)` is unaffected, because the amounts join on `order_id`, not status.
- Every test passed because the `status` checks can't see NULLs:
  - `accepted_values` ignores NULLs.
  - There is no `not_null` test on `status` in either `orders` or `stg_orders`.

The new column also introduces a value outside the accepted set, `delivered` (orders 66 and 67). It would have failed `accepted_values` had it been read.

## 2. Fix

In `stg_orders.sql`:

```sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,
        ordered_at,
        cast(ordered_at as date) as order_date,
        coalesce(status, order_status) as status

    from source

)
```

Then decide what `delivered` means before rebuilding. Once the coalesce is in, the existing `accepted_values` test will fail on it, which is the right outcome. Don't map it to `completed` by assumption. Check by reconciling to the admin figure:

```sql
select
    sum(amount) filter (where status = 'completed') as completed_only,
    sum(amount) filter (where status in ('completed','delivered')) as with_delivered
from orders
where order_date >= '2018-03-01' and order_date < '2018-04-01'
```

Whichever matches 279.00 tells you the mapping. If `delivered` is a distinct state, add it to the accepted values. If it is a synonym, map it in staging with `case when ... = 'delivered' then 'completed'`. After that, run `dbt build` and confirm `sum(completed_lifetime_value)` is 1103.00. Also ask the ingestion owners which column is canonical going forward, so the coalesce isn't a permanent patch.

## 3. Tests that would have caught this

Add `not_null` to `status` in `models/staging/schema.yml` (and in `orders`):

```yaml
      - name: status
        tests:
          - not_null
          - accepted_values:
              arguments:
                values: ['placed', 'shipped', 'completed', 'return_pending', 'returned']
```

That alone would have failed with 43 rows. A reconciliation test is also worth adding. `tests/assert_completed_value_matches_orders.sql`:

```sql
select o.completed, c.completed
from (select sum(amount) as completed from {{ ref('orders') }} where status = 'completed') o
cross join (select sum(completed_lifetime_value) as completed from {{ ref('customers') }}) c
where abs(o.completed - c.completed) > 0.005
```

This only checks `customers` against `orders`, so it wouldn't have caught this particular bug, because both sides read the same NULL status. The `not_null` test is the one that would have. To catch schema drift at the source, add a singular test on `raw_orders` that fails when `status is null` and `order_status is not null`.

I didn't run anything here, since there is no project in my working directory. The diagnosis comes from the files and samples you pasted.
