claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 15 s | USD 0.053 | 2026-10-14 | project 7d5b525+dirty

## 1. Root cause

`raw_orders` (the seed behind `models/staging/stg_orders.sql`) now has two status columns, `status` and `order_status`. The older rows (2018-01 and 2018-02, 56 orders) fill `status`. The newer rows (2018-03 and 2018-04, 43 orders) leave `status` NULL and presumably fill `order_status`. The month breakdown shows this: `with_status` is 29/27 for Jan/Feb and 0/0 for Mar/Apr.

`stg_orders.sql` only reads `status`, so those 43 orders get `status = NULL`. In `customers.sql`, `case when orders.status = 'completed' then amount else 0 end` is false for NULL, so their payments drop out of `completed_lifetime_value`. That is why the total fell from 1103 to 824, while `customer_lifetime_value` and `sum(orders.amount)` still match at 1672. The status breakdown agrees: 761.00 sits under a NULL status, and 1103 − 824 = 279 of that is presumably completed.

Nothing failed because:
- `accepted_values` ignores NULLs.
- There is no `not_null` test on `stg_orders.status` or `orders.status`.
- No test reconciles completed revenue.

`daily_revenue` and `region_revenue` don't use status, so they are unaffected.

## 2. Fix

In `models/staging/stg_orders.sql`:

```sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,
        ordered_at,
        cast(ordered_at as date) as order_date,
        -- upstream started populating order_status instead of status from 2018-03
        coalesce(status, order_status) as status

    from source

)
```

Before rebuilding, check that the `order_status` values fit the accepted list (`select distinct order_status from raw_orders`). If it uses different labels, such as `complete` instead of `completed`, map them here. The `accepted_values` tests will fail loudly if not. Then run `dbt build`. A full refresh isn't needed, since `daily_revenue` doesn't depend on status. Afterwards `sum(completed_lifetime_value)` should be back to 1103.00. I haven't run this, since I don't have the seed data.

## 3. Tests that would have caught it

Add `not_null` on status in both layers (`models/staging/schema.yml` and `models/schema.yml`):

```yaml
      - name: status
        tests:
          - not_null
          - accepted_values:
              arguments:
                values: ['placed', 'shipped', 'completed', 'return_pending', 'returned']
```

Add a singular test, `tests/assert_completed_ltv_matches_orders.sql`, to reconcile the output with the source:

```sql
select c.completed, o.completed
from (select sum(completed_lifetime_value) as completed from {{ ref('customers') }}) c
cross join (select sum(amount) as completed from {{ ref('orders') }} where status = 'completed') o
where abs(c.completed - o.completed) > 0.001
```

The `not_null` test would have failed on 43 rows at staging. The reconciliation test only guards `customers` against `orders`. It would not have caught this particular bug on its own, because both sides read the same NULL status. For that you would need to compare against `raw_orders` directly, or rely on the `not_null` test.
