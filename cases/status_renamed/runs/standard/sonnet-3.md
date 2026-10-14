claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 20 s | USD 0.086 | 2026-10-15 | project ad0b16b | prompt c1e2f11386cd

## 1. Root cause

**`models/staging/stg_orders.sql`** reads only `raw_orders.status`. The raw table now has two status columns, `status` and `order_status`, and the feed has moved to the new one:

- For 43 orders (the newer ones, from about late March), `status` is `NULL` and the real value is in `order_status`. The sample shows order 84 as `NULL` / `placed`, and order 78 as `NULL` / `shipped`.
- Older orders have both columns filled and agreeing, for example order 33 is `completed` / `completed`.
- Orders with `status = NULL` don't match `orders.status = 'completed'` in `customers.sql`. The `case when` falls through to `0`, so their payments drop out of `completed_lifetime_value`.
- That is why the total fell from 1103 to 824 while `sum(orders.amount)` stayed at 1672. `orders` has a `NULL` status bucket of 43 orders and 761.00.
- The 279.00 gap (1103 − 824) should equal the amount of the `NULL`-status orders whose `order_status` is `completed`. I couldn't run that query here, so please confirm it with the check below.

Every test passed because none of them can see this:

- `accepted_values` on `status` ignores `NULL`s, since `NULL not in (...)` is not true.
- There is no `not_null` test on `status` in either `stg_orders` or `orders`.

## 2. Fix

In `models/staging/stg_orders.sql`:

```sql
    select
        id as order_id,
        user_id as customer_id,
        ordered_at,
        cast(ordered_at as date) as order_date,
        coalesce(status, order_status) as status

    from source
```

Rebuilding with `dbt build` should give `completed_lifetime_value` back at 1103.00, and the `NULL` row in `orders` by status should disappear.

Before relying on this, check that the two columns never disagree when both are set:

```sql
select count(*) from raw_orders
where status is not null and order_status is not null and status <> order_status
```

If that returns a non-zero count, decide which column wins. Ask the feed owner to confirm which column is authoritative going forward.

## 3. Tests that would have caught it

Add `not_null` to the status columns:

```yaml
# models/staging/schema.yml, under stg_orders
      - name: status
        tests:
          - not_null
          - accepted_values:
              arguments:
                values: ['placed', 'shipped', 'completed', 'return_pending', 'returned']
```

Add the same `not_null` to `orders.status` in `models/schema.yml`.

Add a singular test, `tests/assert_completed_ltv_reconciles.sql`, that checks the totals against the raw data rather than through the same staging logic:

```sql
-- completed revenue in customers must equal completed payments computed straight from the raw tables
with expected as (
    select sum(p.amount) / 100.0 as amt
    from {{ ref('raw_payments') }} p
    join {{ ref('raw_orders') }} o on p.order_id = o.id
    where coalesce(o.status, o.order_status) = 'completed'
),
actual as (
    select sum(completed_lifetime_value) as amt from {{ ref('customers') }}
)
select * from expected, actual
where abs(expected.amt - actual.amt) > 0.005
```

To catch schema drift earlier, add a source-level test on `raw_orders` that fails when `status` is null and `order_status` is not. A `dbt_utils.expression_is_true` on the seed would do it.
