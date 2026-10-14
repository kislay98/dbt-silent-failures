claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 26 s | USD 0.006 | 2026-10-15 | project ad0b16b | prompt c4a4ba955988

## 1. Root cause

**`jaffle_shop/models/staging/stg_orders.sql`** reads only `status` from `raw_orders`, but the ingested table now has a second column, `order_status`. The raw table shows the two columns diverging:

- `status` is NULL for 43 orders (amount 761.00). Those rows have `order_status` values such as `placed` and `delivered`.
- `status` is `completed` for the 50 orders that still have it (amount 824.00).

`customers.completed_lifetime_value` and the `orders` status breakdown both filter on `status = 'completed'`, so every order whose `status` went NULL drops out of completed revenue. The numbers reconcile:

- 1103.00 (last week) − 824.00 (now) = 279.00, the March completed orders the admin still shows.
- 824 + 761 (NULL status) + 49 (returned) + 38 (return_pending) = 1672, which matches `sum(orders.amount)`.

So nothing was returned or cancelled. The upstream feed stopped populating `status` for some orders (the 279 March orders are the obvious candidates), and the model silently treats them as not completed.

**Why the tests pass:** the only check on `status` is `accepted_values` in `models/staging/schema.yml` and `models/schema.yml`. dbt's `accepted_values` test ignores NULLs, so NULL statuses never fail it. Nothing tests `status` for `not_null`, and nothing tests `completed_lifetime_value` against an expected total.

**Secondary issue:** `order_status` contains `delivered`, which is not in the model's accepted list (`placed, shipped, completed, return_pending, returned`). Whatever fix you choose has to decide what `delivered` means for revenue.

## 2. Fix

First, confirm which `order_status` values the NULL-status orders carry. Run this against the warehouse:

```sql
select order_status, count(*) as orders, round(sum(amount), 2) as amount
from stg_orders o
join stg_payments p using (order_id)   -- or use raw_orders + raw_payments amounts
where status is null
group by 1;
```

If the March orders are `order_status = 'completed'`, `coalesce` alone restores the 1103.00. If they are `delivered`, the fix needs a mapping, and the business owner has to confirm that `delivered` means completed.

Change `stg_orders.sql`:

```sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,
        ordered_at,
        cast(ordered_at as date) as order_date,

        -- `status` is no longer populated for every order; fall back to `order_status`
        -- `delivered` is mapped to `completed` ONLY if finance confirms that is the meaning
        case coalesce(status, order_status)
            when 'delivered' then 'completed'
            else coalesce(status, order_status)
        end as status

    from source

)
```

After the rebuild, check that `sum(customers.completed_lifetime_value)` is 1103.00 and that `orders` has no NULL `status`. If the `delivered` mapping is not approved, leave it out. The `accepted_values` test will then fail on `delivered`, which is the correct signal.

## 3. Test that would have caught it

Add `not_null` to `status` in `models/staging/schema.yml`. Keep the existing `accepted_values` test, because it still catches unknown values:

```yaml
  - name: stg_orders
    columns:
      - name: status
        tests:
          - not_null
          - accepted_values:
              arguments:
                values: ['placed', 'shipped', 'completed', 'return_pending', 'returned']
```

With this test, the NULL statuses would have failed the nightly build instead of passing silently.

Also consider a singular test, `tests/assert_completed_revenue_matches_orders.sql`, which fails if completed revenue in `customers` drops more than expected between runs. A dbt test cannot compare against last week's value, so this needs either a stored baseline or a check against `raw_payments` for completed orders.

**Unrelated doc drift:** `models/schema.yml` documents `customers.total_order_amount`, but `customers.sql` does not produce that column. You may want to remove it.
