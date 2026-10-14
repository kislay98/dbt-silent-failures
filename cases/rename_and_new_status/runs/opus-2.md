claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 24 s | USD 0.123 | 2026-10-14 | project f769fdf+wk6ext | prompt 90dc0b383fa7

## 1. Root cause

The upstream `raw_orders` table changed shape. The models were not changed.

- The ingestion job added a new column, `raw_orders.order_status`. From the 2018‑03‑01 orders onward (order 57+), it writes the status **only** to `order_status` and leaves the old `status` column NULL. Orders 54–56 have both columns filled with identical values, so the switch happened around the end of February.
- `models/staging/stg_orders.sql` still selects `status`. Every March and April order (35 + 8 = 43 rows) therefore reaches `stg_orders`, `orders`, and `customers` with `status = NULL`. You can see this in the output sample: `with_status = 0` for 2018‑03 and 2018‑04, and 43 NULL-status orders worth 761.00.
- In `models/customers.sql`, `case when orders.status = 'completed'` is false for NULL. March's completed orders drop out of `completed_lifetime_value`: 1103 − 824 = **279**, exactly the March completed figure finance quoted. `amount` comes from payments and never uses status, so `sum(orders.amount)` stays at 1672.
- No test failed because `accepted_values` ignores NULLs, and nothing asserts `status` is `not_null`. The new feed also uses a value the project has never seen, `delivered` (orders 66 and 67). That would have failed `accepted_values` if it had ever reached the `status` column.

## 2. Fix

In `models/staging/stg_orders.sql`, read the new column and fall back to the old one for historical rows:

```sql
renamed as (

    select
        id as user_id_placeholder_removed,  -- (unchanged lines below)
```

More precisely, change only the `status` line:

```sql
        cast(ordered_at as date) as order_date,
        coalesce(order_status, status) as status
```

Then run `dbt build`. Expect one more failure after the fix: `accepted_values` on `stg_orders.status` and `orders.status` will fail on `delivered`. That failure is a real question, not noise. I would not silently map `delivered` to `completed`. First check which interpretation matches the shop admin's 279.00 for March:

```sql
select o.order_status, sum(p.amount) / 100 as amount
from raw_orders o join raw_payments p on p.order_id = o.id
where o.ordered_at >= '2018-03-01' and o.ordered_at < '2018-04-01'
group by 1;
```

- If `completed` alone sums to 279, `delivered` is a distinct state. Add it to the `accepted_values` lists in both schema files, and confirm with the business whether it should count toward completed revenue.
- If 279 only appears when you add `completed` and `delivered`, map it in staging, for example `case when coalesce(order_status, status) = 'delivered' then 'completed' else coalesce(order_status, status) end`, or count both in `customers.sql`.

Also tell the ingestion team. Either they keep populating `status`, or `order_status` becomes the documented column and the old one gets dropped.

## 3. Tests that would have caught it

In `models/staging/schema.yml`, and the same under `orders` in `models/schema.yml`:

```yaml
      - name: status
        tests:
          - not_null
          - accepted_values:
              arguments:
                values: ['placed', 'shipped', 'completed', 'return_pending', 'returned']
```

`not_null` would have failed on the first build after the change, with 43 rows. `accepted_values` alone can never catch this kind of problem, because it ignores NULLs.

To catch the two columns disagreeing during the overlap period, add a singular test, `tests/raw_orders_status_consistent.sql`:

```sql
-- fails if the old and new status columns disagree, or neither is populated
select id, status, order_status
from {{ ref('raw_orders') }}
where coalesce(order_status, status) is null
   or (status is not null and order_status is not null and status <> order_status)
```

Optionally, you can also add a schema-drift guard, such as `dbt_expectations.expect_table_columns_to_match_set` on `raw_orders`, so a new upstream column fails the build instead of being silently ignored.
