claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 27 s | USD 0.005 | 2026-10-14 | project f769fdf+wk6ext | prompt 56722c109409

## 1. Root cause

The bug is in `jaffle_shop/models/staging/stg_orders.sql`, line 18. It reads `status` from `raw_orders`, but the ingestion job has moved the live status into a new column, `order_status`. `status` is still in the table but has been NULL since about 2018-03-01 (order id 57 onward).

The output confirms this:

- `orders` has 43 rows with `status` NULL, totalling 761.00. Those are the March and April orders.
- `completed` drops to 50 orders and 824.00, from the 1103.00 finance expected.
- 1103.00 − 824.00 = 279.00, which is exactly the March completed revenue the admin shows.
- `with_status` is 0 for 2018-03 and 2018-04.
- In the raw sample, `order_status` is `completed` for ids 57–61, where `status` is NULL.

`customers.sql` computes `completed_lifetime_value` from `orders.status = 'completed'`, so every March completed order falls out of it. `sum(orders.amount)` is unaffected because amounts come from payments.

**Why the tests passed:** `accepted_values` ignores NULLs, and nothing tests `status` for not-null (neither `staging/schema.yml` nor `schema.yml` has one). No test compares the staged status with the source.

**Other problems to fix alongside:**

- `delivered` (ids 66, 67) appears in `order_status` but is not in the `accepted_values` list. Once `stg_orders` reads `order_status`, that test will fail. Finance needs to decide whether `delivered` counts as completed. I have not assumed it does: the admin's 1103.00 figure suggests the delivered orders are not counted today.
- `schema.yml` documents `total_order_amount` on `customers`, but the model has no such column.

## 2. Fix

Before switching columns, confirm that `status` and `order_status` agree on every row where both are populated. I only saw ids 54–56, so run this against the raw table:

```sql
select id, status, order_status
from raw_orders
where status is not null and status <> order_status;
```

If that returns no rows, change the staging model to read the live column:

```sql
-- models/staging/stg_orders.sql, renamed CTE
select
    id as order_id,
    user_id as customer_id,
    ordered_at,
    cast(ordered_at as date) as order_date,
    coalesce(status, order_status) as status
from source
```

`coalesce(status, order_status)` gives the same answer for the old rows and fills in the new ones. Once the ingestion owner confirms that `order_status` is the replacement, change it to just `order_status as status` and drop `status`.

Then rebuild everything downstream of staging:

```
dbt build --select stg_orders+
```

`customers` is a table, so it is rebuilt by this. `daily_revenue` does not use status, so it needs no change. After the rebuild, `completed` should be 1103.00 and the March share should be 279.00.

Map `delivered` only after finance confirms what it means. Adding it to `accepted_values` alone would hide the question.

## 3. Test that would have caught it

Add a not-null test to the staging model and to `orders`, in `models/staging/schema.yml` and `models/schema.yml`:

```yaml
      - name: status
        tests:
          - not_null
```

Add a test that the staged status matches the source. Put this in `tests/assert_status_matches_source.sql`:

```sql
-- Fails if staging loses or changes an order status compared with the raw feed
select r.id
from {{ ref('raw_orders') }} r
join {{ ref('stg_orders') }} s
    on s.order_id = r.id
where coalesce(r.status, r.order_status) is distinct from s.status
```

The not-null test alone would have caught this on the first run. The reconciliation test also catches a silently wrong fallback, and it will flag `delivered` (or any other unmapped value) once it reaches staging.
