claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 24 s | USD 0.171 | 2026-10-15 | project ad0b16b | prompt c4a4ba955988

## 1. Root cause

The ingestion job changed the shape of `raw_orders`, and `models/staging/stg_orders.sql` still reads the old column.

- `raw_orders` now has a new column, `order_status`, next to the legacy `status`. Older rows have both filled in and they agree (`completed` / `completed`). Newer rows, from about March onward, only fill `order_status`, so `status` is **NULL** for 43 orders (761.00).
- The new feed also uses different values. Orders the shop admin counts as completed arrive as **`delivered`**, not `completed`. `placed` also appears.
- `stg_orders.sql` passes `status` through unchanged. `customers.sql` only counts `orders.status = 'completed'`, so the 279.00 of March completed orders now has status NULL and drops out: 1103.00 − 279.00 = **824.00**.
- `sum(amount)` stays at 1672.00 because amounts and payments are unaffected. Only the status label is lost.

**Why every test passed:** `accepted_values` ignores NULLs; the test only checks rows `where status is not null`. There is no `not_null` on `status` anywhere, and no test looks at `raw_orders.order_status`, so `delivered` is never checked.

Before applying the fix, confirm the value mapping against the admin figure:

```sql
select order_status, count(*), sum(p.amount) / 100 as amount
from raw_orders o join raw_payments p on p.order_id = o.id
where o.status is null
group by 1;
-- expect: delivered ≈ 279.00
```

## 2. Fix: `models/staging/stg_orders.sql`

Read the new column, translate its values back to the model's vocabulary, and fall back to the legacy column:

```sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,
        ordered_at,
        cast(ordered_at as date) as order_date,

        -- the feed moved status to order_status and renamed 'completed' to 'delivered'
        coalesce(
            case order_status
                when 'delivered' then 'completed'
                else order_status
            end,
            status
        ) as status

    from source

)
```

Then run `dbt build`. `customers` is a full table, so it rebuilds correctly. `daily_revenue` does not use status and needs no backfill.

Any other renamed value I haven't seen would make the existing `accepted_values` test fail after this change. That failure is what you want; add the value to the `case` when it shows up. Also tell the ingestion owners about the schema change, and ask whether `status` is being retired.

## 3. Tests that would have caught it

In `models/staging/schema.yml`, add `not_null` to `stg_orders.status`. This alone would have failed with 43 rows:

```yaml
      - name: status
        tests:
          - not_null
          - accepted_values:
              arguments:
                values: ['placed', 'shipped', 'completed', 'return_pending', 'returned']
```

Add the same `not_null` to `orders.status` in `models/schema.yml`.

To catch value changes at the source, declare `raw_orders` (seed or source) with `accepted_values` on `order_status`. That test would have flagged `delivered` immediately.

For a revenue reconciliation guard, add a singular test, `tests/assert_all_revenue_has_known_status.sql`:

```sql
select count(*) as unclassified_orders
from {{ ref('orders') }}
where status is null
having count(*) > 0
```
