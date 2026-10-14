claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 37 s | USD 0.197 | 2026-10-15 | project ad0b16b | prompt 1b2ae9e32b52

# Completed revenue excludes the new `delivered` status

## 1. Root cause

The upstream shop system started sending a new order status, `delivered`, in March 2018. The project's tests were changed to accept it, but the revenue logic was never updated to count it as completed.

**The numbers match exactly:**

| | Admin | dbt (`status = 'completed'`) | Gap |
|---|---|---|---|
| Since January | 1103.00 | 967.00 | **136.00** |
| March | 279.00 | 143.00 | **136.00** |

- The `orders` status breakdown shows `delivered | 8 orders | 136.0`.
- The gap is the same 136.00 in both rows, so all 8 `delivered` orders are dated March. The sample confirms this: order 75 (2018-03-18) is `delivered`, and order 63 (2018-03-06) is still `completed`.
- Total revenue still matches because `amount` doesn't depend on status.

**Where it goes wrong:**

- **`raw_orders` (data):** 8 March orders, worth 136.00, have `status = 'delivered'`. The shop admin counts these as completed. Every completed-revenue calculation in the project only looks for the literal string `'completed'`.
- **`models/customers.sql`:** the line `sum(case when orders.status = 'completed' then amount else 0 end) as completed_amount` drops those orders, so `completed_lifetime_value` sums to 967. Finance's own `orders.status = 'completed'` filter drops them the same way, which gives the 143 for March.
- **`models/staging/schema.yml` and `models/schema.yml`:** both `accepted_values` tests on `status` were widened to include `'delivered'`. That was the one test that would have caught the change, and widening it is why `dbt build` reports PASS=35.

The other models are fine. `orders`, `daily_revenue` and `region_revenue` all reconcile to the same monthly and total figures (1672.00).

## 2. Fix

Map the new status to the existing one in staging, so every downstream model (`customers.sql`) and every ad-hoc `status = 'completed'` query is fixed in one place. The original value is kept for auditing.

`models/staging/stg_orders.sql`:
```sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,
        ordered_at,
        cast(ordered_at as date) as order_date,
        status as source_status,
        -- the shop system sends 'delivered' for completed orders from March 2018; the admin counts both as completed
        case when status = 'delivered' then 'completed' else status end as status

    from source

)
```

Then run `dbt build` (or at least `dbt build -s stg_orders+`). Expected results:
- `customers.completed_lifetime_value` sums to 1103.00.
- March `completed` in `orders` sums to 279.00.

Before shipping, confirm with the shop or ingestion team that `delivered` is a rename of `completed` and not a separate pre-completion state (for example, one that can still become `returned`). The admin's totals suggest it is a rename. If it turns out to be a separate state, keep `status` as it is and add an `is_completed` flag (`status in ('completed', 'delivered')`) for `customers.sql` and finance to use instead.

## 3. Tests that would have caught this

Remove `'delivered'` from the canonical status list again, in both schema files. The list should describe the statuses downstream logic actually handles, not whatever the source happens to send:

```yaml
      - name: status
        tests:
          - accepted_values:
              arguments:
                values: ['placed', 'shipped', 'completed', 'return_pending', 'returned']
```

With this list, the first nightly run that contained a `delivered` order would have failed on 8 rows instead of passing. To get early warning on raw values the mapping doesn't yet cover, add this on the raw column in `stg_orders`:

```yaml
      - name: source_status
        tests:
          - accepted_values:
              arguments:
                values: ['placed', 'shipped', 'completed', 'delivered', 'return_pending', 'returned']
              config:
                severity: warn
```

The convention to agree on: a new upstream value gets added to this list only together with a decision in the `case` mapping. It should never be added to the canonical list just to make the build go green.

**Minor, unrelated:** `models/schema.yml` documents a column `total_order_amount` on `customers` that doesn't exist. The actual column is `customer_lifetime_value`.
