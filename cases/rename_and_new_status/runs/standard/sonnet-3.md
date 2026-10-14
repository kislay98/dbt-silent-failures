claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 21 s | USD 0.088 | 2026-10-15 | project ad0b16b | prompt c4a4ba955988

## 1. Root cause

**File: `models/staging/stg_orders.sql`** (the upstream issue is in the `raw_orders` load).

`raw_orders` now has two status columns, `status` and a new `order_status`. The ingestion job stopped filling `status` for orders from March onward and puts the value in `order_status` instead. `stg_orders` still reads only `status`, so every order since 2018-03-01 gets `status = NULL`.

The numbers show this:
- `orders` has 43 rows with `status = NULL`, summing to 761.00. That is exactly March (622) plus April (139).
- `customers.completed_lifetime_value` is 824.00 because `case when status = 'completed'` never matches a NULL. 1103 − 824 = 279, which is March's completed total in the shop admin.
- The new feed also uses a new vocabulary. The raw sample has `order_status` values `delivered` and `placed` on rows where `status` is NULL. `delivered` is not in the accepted list.
- `sum(orders.amount)` stays at 1672.00 because the joins and payments are unaffected. Only the status filter loses rows.

I'm inferring `delivered` → `completed` from the numbers (279 of March was completed). I have no access to the raw data, so confirm it with `select order_status, count(*), sum(p.amount) from raw_orders ... where status is null group by 1`.

**Why every test passed:** `accepted_values` ignores NULLs, and nothing has `not_null` on `status`. Nothing reconciles `customers` against `orders` either.

## 2. Fix

In `models/staging/stg_orders.sql`:

```sql
    select
        id as order_id,
        user_id as customer_id,
        ordered_at,
        cast(ordered_at as date) as order_date,
        -- the feed moved status into order_status from March and renamed 'completed' to 'delivered'
        coalesce(
            status,
            case order_status
                when 'delivered' then 'completed'
                else order_status
            end
        ) as status

    from source
```

Then run `dbt build`. `customers` is a table, so it rebuilds in full. `daily_revenue` doesn't use status, so it is unaffected.

After the rebuild, `sum(completed_lifetime_value)` should be 1103.00. Also ask the ingestion owners which column is canonical going forward, and whether `delivered` is meant to be `completed` or a new state between `shipped` and `completed`.

## 3. Tests that would have caught this

Add `not_null` to the status column in `models/staging/schema.yml` (and the same in `models/schema.yml` for `orders`):

```yaml
      - name: status
        tests:
          - not_null
          - accepted_values:
              arguments:
                values: ['placed', 'shipped', 'completed', 'return_pending', 'returned']
```

With this, the original build would have failed on 43 NULL rows. Once the NULLs are filled, the same `accepted_values` test catches any new vocabulary such as `delivered`.

Add a reconciliation test, `tests/assert_customers_completed_ltv_matches_orders.sql`:

```sql
select
    c.completed_ltv,
    o.completed_amount
from (select sum(completed_lifetime_value) as completed_ltv from {{ ref('customers') }}) c
cross join (select sum(amount) as completed_amount from {{ ref('orders') }} where status = 'completed') o
where abs(c.completed_ltv - o.completed_amount) > 0.005
```

The `not_null` test above is the one that would have caught this incident. The reconciliation test guards against `customers` and `orders` drifting apart in future. To catch a feed that stops marking orders completed, also add a `dbt_utils.expression_is_true` or `source freshness`-style check that completed orders exist in each recent month.
