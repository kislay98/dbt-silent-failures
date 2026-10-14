claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 31 s | USD 0.099 | 2026-10-14 | project ad0b16b | prompt 3b4c114442ec

## 1. Root cause

**The raw order feed was loaded twice for part of February.** `raw_orders` (seed/ingestion table, read by `models/staging/stg_orders.sql`) has 16 replayed rows. Each has a new `id` (100 to 115) but the same `user_id` and `ordered_at` as an original order. The sample shows one pair:

- Orders 41 and 105 are both customer 99 at `2018-02-14 10:44:12`.
- Orders 112, 113 and 114 look like further replays (customer 51 on 2018-02-23, for example).

`raw_payments` has matching duplicate payments for those ids, so each replay carries its own payment lines. That gives:

- **Order counts:** 115 − 16 = 99, which matches the admin.
- **Revenue:** 1913 − 241 = 1672, which matches the processor.
- **Customer 51:** 5 − 2 = 3 orders.

Every duplicate falls in February, which is why `orders` has 43 February orders and `daily_revenue` has 27.

**Why the tests passed:** `unique` on `order_id` only checks the surrogate key, and the replays have fresh ids. Nothing checks the natural key (`customer_id`, `ordered_at`). Nothing reconciles the marts against each other.

**Why `daily_revenue` disagrees:** it is incremental and only rebuilds the last 3 days (`max(order_date) - 3 days`). It was built before the replay landed, and February is far outside that window, so it keeps the correct old numbers. It is right by accident. The `orders`, `customers` and `region_revenue` tables are all rebuilt in full, so they absorbed the duplicates. Region revenue also sums to 1913, and its 115 orders is the same overcount.

## 2. Fix

Fix the ingestion job so it stops replaying batches, then remove the duplicates in staging. I haven't run any of this, since the project files aren't in my working directory.

First confirm the diagnosis:

```sql
select user_id, ordered_at, count(*), min(id), max(id)
from raw_orders group by 1, 2 having count(*) > 1
```

This should return 16 extra rows. Also check that the duplicate payments have the same amounts as the originals.

`stg_orders.sql`: keep the earliest id per natural key.

```sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,
        ordered_at,
        cast(ordered_at as date) as order_date,
        status,
        row_number() over (partition by user_id, ordered_at order by id) as rn

    from source

),

deduped as (

    select order_id, customer_id, ordered_at, order_date, status
    from renamed
    where rn = 1

)

select * from deduped
```

`stg_payments.sql`: drop payments that belong to the removed duplicate orders.

```sql
    from source
    where order_id in (select order_id from {{ ref('stg_orders') }})
```

`daily_revenue`: the 3-day lookback can't repair history. The table has only 69 rows, so make it a plain table (drop the `config` block and the `is_incremental()` filter), or at minimum run it once with `--full-refresh`.

```
dbt build --full-refresh
```

Expected afterwards: 99 orders, 1672.00 in `orders`, `customers`, `region_revenue` and `daily_revenue`, and customer 51 with 3 orders.

## 3. Tests that would have caught it

Natural-key uniqueness on staging (requires `dbt_utils`):

```yaml
  - name: stg_orders
    data_tests:
      - dbt_utils.unique_combination_of_columns:
          arguments:
            combination_of_columns: [customer_id, ordered_at]
```

Payments must reference a real order, in `staging/schema.yml`:

```yaml
      - name: stg_payments
        columns:
          - name: order_id
            tests:
              - relationships:
                  arguments:
                    to: ref('stg_orders')
                    field: order_id
```

A singular test, `tests/daily_revenue_matches_orders.sql`, that reconciles the marts. It would have flagged the 16 February days where `daily_revenue` and `orders` disagree:

```sql
with o as (
    select order_date, count(*) as orders, sum(amount) as revenue
    from {{ ref('orders') }} group by 1
)
select *
from o
full join {{ ref('daily_revenue') }} d using (order_date)
where o.orders is distinct from d.orders
   or o.revenue is distinct from d.revenue
```

The test above also needs `{{ config(severity='error') }}` if you want it to block the build.
