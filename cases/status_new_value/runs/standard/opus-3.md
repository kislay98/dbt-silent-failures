claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 33 s | USD 0.188 | 2026-10-15 | project ad0b16b | prompt 1b2ae9e32b52

# Root cause: a new `delivered` status that the models don't count as completed

## 1. Root cause

The source system has started writing **`delivered`** for orders it previously marked **`completed`**. The shop admin counts `delivered` as completed, but the project only counts the literal string `'completed'`.

The numbers match this exactly:

| | Admin | dbt (`status = 'completed'`) | Difference |
|---|---|---|---|
| Since January | 1103.00 | 967.00 | **136.00** |
| March | 279.00 | 143.00 | **136.00** |

From your status breakdown, the `delivered` orders in `orders` total **8 orders, 136.00**. So every one of them is in March, which is where the switch from `completed` to `delivered` happened. The sample is consistent: order 63 on 2018-03-06 is `completed`, and order 75 on 2018-03-18 is `delivered`. Total revenue is unaffected because it doesn't filter on status, which is why every month's total still matches the admin.

The files involved:

- **`models/customers.sql`**: `sum(case when orders.status = 'completed' ...)` silently drops `delivered` orders. Any "completed orders" query against `orders.status` has the same blind spot.
- **`models/staging/schema.yml`** and **`models/schema.yml`**: both `accepted_values` tests on `status` list `'delivered'`. When the new value appeared, someone added it to the allowed list to make the test pass, but no model was updated to handle it. That is why the build is green.

Two side notes, neither of which causes this: the region, fee and daily-revenue figures all reconcile to the totals. Separately, `schema.yml` documents a column `total_order_amount` on `customers` that doesn't exist (it's `customer_lifetime_value`).

To confirm the timing before fixing:

```sql
select status, min(ordered_at), max(ordered_at), count(*)
from raw_orders where status in ('completed', 'delivered') group by 1;
```

## 2. Fix

Normalize the status once in staging so every downstream model agrees with the admin. Keep the raw value for traceability.

`models/staging/stg_orders.sql`:

```sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,
        ordered_at,
        cast(ordered_at as date) as order_date,
        -- the shop started emitting 'delivered' for completed orders in March 2018;
        -- the admin treats both as completed
        case when status = 'delivered' then 'completed' else status end as status,
        status as source_status

    from source

)
```

Then remove `'delivered'` from both `accepted_values` lists on `status`, in `models/staging/schema.yml` and `models/schema.yml`. After a rebuild, `customers.completed_lifetime_value` should sum to 1103.00, and March completed orders should total 279.00.

Do a full refresh of the incremental model as a precaution: `dbt build --full-refresh -s daily_revenue`. It doesn't use status today, but this keeps it consistent if it ever does.

Before shipping, confirm with whoever owns the shop data that `delivered` really is equivalent to `completed`. If it is a genuinely different lifecycle stage, use `status in ('completed', 'delivered')` in `customers.sql` instead. The admin numbers suggest they are equivalent.

## 3. Tests that would have caught it

**a) A strict source-level test that stays strict.** Test the raw value with only the statuses the models know how to handle. Then a new value fails the build instead of being waved through:

```yaml
# models/staging/schema.yml
  - name: stg_orders
    columns:
      - name: source_status
        tests:
          - accepted_values:
              arguments:
                values: ['placed', 'shipped', 'completed', 'return_pending', 'returned', 'delivered']
      - name: status
        tests:
          - accepted_values:
              arguments:
                # only statuses downstream logic handles; a new source value must be mapped above first
                values: ['placed', 'shipped', 'completed', 'return_pending', 'returned']
```

The real control is the comment, plus a code-review rule: adding a value to `accepted_values` requires a matching change in the model logic.

**b) A reconciliation test** that ties the completed metric to the order statuses the business counts as completed. `tests/assert_completed_ltv_reconciles.sql`:

```sql
with ltv as (
    select coalesce(sum(completed_lifetime_value), 0) as amt from {{ ref('customers') }}
),
src as (
    select coalesce(sum(p.amount), 0) as amt
    from {{ ref('stg_payments') }} p
    join {{ source_or_ref_raw_orders }} o on p.order_id = o.id
    where o.status in ('completed', 'delivered')   -- business definition, owned by finance
)
select * from ltv, src where abs(ltv.amt - src.amt) > 0.01
```

Replace `{{ source_or_ref_raw_orders }}` with `{{ ref('raw_orders') }}`. This test reads raw statuses against finance's definition, so it would have failed with 967 against 1103 on the first night `delivered` appeared.
