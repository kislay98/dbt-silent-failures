claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 22 s | USD 0.005 | 2026-10-15 | project ad0b16b | prompt 6cd8da3825cf

## 1. Root cause

The raw `status` values are mixed-case, and `customers.sql` compares them case-sensitively.

The `orders` status breakdown shows two versions of the same status:

| status | orders | amount |
|---|---|---|
| `completed` | 36 | 604.0 |
| `Completed` | 31 | 499.0 |

The finance ledger's 1103.00 is 604.00 + 499.00, so it counts both spellings. `customers.completed_lifetime_value` only sums `status = 'completed'`, so it gets 604.00.

The bug starts in `jaffle_shop/models/staging/stg_orders.sql`. It passes `status` through unchanged (`status` in the `renamed` CTE), so the capitalized values reach `orders` and then `customers`. The filter that drops them is in `jaffle_shop/models/customers.sql`, in `customer_payments`:

```sql
sum(case when orders.status = 'completed' then amount else 0 end) as completed_amount
```

Nothing else is wrong with the numbers. `sum(orders.amount)` is 1672.00 and matches `customer_lifetime_value`, so the payment join and the amounts are fine. Only the status filter is affected.

The tests didn't catch it because `accepted_values` in both `jaffle_shop/models/staging/schema.yml` and `jaffle_shop/models/schema.yml` lists `'Completed'`, `'Placed'`, and the other capitalized values as valid. That treats mixed casing as expected, so the test passes on bad data. The `completed_lifetime_value` description ("status completed") shows the intended meaning is the lowercase, case-insensitive one, so the model is wrong, not the docs.

## 2. Fix

Normalize the status once, in staging, so every downstream model sees one spelling. Don't edit the seed, since the feed itself is mixed-case.

`jaffle_shop/models/staging/stg_orders.sql`:

```sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,
        ordered_at,
        cast(ordered_at as date) as order_date,
        -- the source feed mixes casing ('completed' vs 'Completed'), so normalize here
        lower(status) as status

    from source

)
```

Then tighten the accepted values to lowercase only, in both `jaffle_shop/models/staging/schema.yml` and `jaffle_shop/models/schema.yml` (`orders.status`):

```yaml
values: ['placed', 'shipped', 'completed', 'return_pending', 'returned']
```

No change is needed in `customers.sql`. After the fix, `completed_lifetime_value` should sum to 1103.00 (604.00 + 499.00). `orders.amount` and `region_revenue` don't depend on status and should not change.

I haven't run this. The numbers above come from the pasted output, so run `dbt build` and re-check the sum to confirm.

## 3. A test that would have caught it

The lowercase-only `accepted_values` test is the one that would have failed. On the current data it fails on the `Completed` rows, which is the bug, and it would keep failing if mixed casing came back in a future load.

I'd also add a singular test that recomputes completed revenue from `orders` with a case-insensitive filter and compares it with `customers`. It would have shown 604 against 1103.

`jaffle_shop/tests/assert_completed_lifetime_value_matches_orders.sql`:

```sql
-- Fails when customers.completed_lifetime_value disagrees with a case-insensitive
-- sum of completed order amounts
with expected as (

    select
        customer_id,
        sum(amount) as completed_amount

    from {{ ref('orders') }}
    where lower(status) = 'completed'
    group by customer_id

)

select
    customers.customer_id,
    customers.completed_lifetime_value,
    expected.completed_amount

from {{ ref('customers') }} as customers
left join expected
    on customers.customer_id = expected.customer_id

where round(coalesce(customers.completed_lifetime_value, 0), 2)
    <> round(coalesce(expected.completed_amount, 0), 2)
```

Note that this test uses `lower()` in its own filter, so after the staging fix it guards the output without depending on the same casing assumption as `customers.sql`.

Separately, `jaffle_shop/models/schema.yml` documents a `total_order_amount` column on `customers` that the model does not produce. It's documentation drift, not the cause here, but it should be corrected.

I can apply the staging change, the schema updates, and the new test if you want.
