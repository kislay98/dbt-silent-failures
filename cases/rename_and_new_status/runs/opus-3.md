claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 25 s | USD 0.123 | 2026-10-14 | project f769fdf+wk6ext | prompt 90dc0b383fa7

## 1. Root cause

**The ingestion job started writing order status to a new column, `raw_orders.order_status`, and stopped populating `raw_orders.status` for orders from 2018-03-01 onwards.** `models/staging/stg_orders.sql` still selects `status`, so every March and April order has `status = NULL` in `stg_orders`, `orders`, and `customers`.

Evidence from your output:
- Orders by month: `with_status` drops to 0 for 2018-03 (35 orders) and 2018-04 (8 orders). Those 43 orders total 622 + 139 = 761, which is exactly the `NULL` status bucket.
- In `raw_orders`, ids 54–56 (February) have both columns filled. From id 57 (2018-03-01) on, `status` is NULL and only `order_status` has a value.
- `customers.sql` counts an order as completed only when `orders.status = 'completed'`. So March's completed orders drop out: 1103 − 824 = **279**, which matches finance's March figure. `sum(amount)` is unaffected because amounts come from payments, so it stays at 1672.

**Why every test passed:**
- `accepted_values` on `status` (in both `staging/schema.yml` and `schema.yml`) ignores NULLs, because `NULL not in (...)` is never true.
- There is no `not_null` test on `status` anywhere.

**Second change in the feed:** `order_status` contains a value the project has never seen, **`delivered`** (orders 66 and 67). It is not in the accepted values list.

## 2. Fix

In `models/staging/stg_orders.sql`, read the new column and fall back to the old one:

```sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,
        ordered_at,
        cast(ordered_at as date) as order_date,
        coalesce(order_status, status) as status

    from source

)
```

Before shipping, confirm the two columns never disagree where both are set, and check that the March reconciliation matches finance:

```sql
-- should return 0 rows
select * from raw_orders where status is not null and order_status is not null and status <> order_status;

-- should return 279.00
select sum(p.amount) / 100.0
from raw_payments p join raw_orders o on p.order_id = o.id
where o.order_status = 'completed' and o.ordered_at >= '2018-03-01' and o.ordered_at < '2018-04-01';
```

That second query decides how to treat `delivered`:
- **If it returns 279:** `delivered` is a separate, non-completed state. Add it to both `accepted_values` lists and to the `orders_status` doc block.
- **If it returns less than 279:** finance counts `delivered` as completed. Ask the shop or ingestion team what `delivered` means before mapping it to `completed`. Don't guess.

After the fix and a `dbt build`, `sum(customers.completed_lifetime_value)` should be back to 1103.00.

## 3. Tests that would have caught it

Add `not_null` on `status` in `models/staging/schema.yml`, and repeat it on `orders.status` in `models/schema.yml`:

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

This would have failed on 43 rows in this week's build. Once the fix is in, `accepted_values` will also fail on `delivered`, which forces the mapping decision above instead of letting new values through silently.

For extra protection, add a `models: stg_orders: config: contract: {enforced: true}` with a `not_null` constraint on `status`. You could also add a singular test that alerts when the completed share of any month falls to zero, for example `tests/assert_every_month_has_completed_orders.sql`.

Unrelated issue I noticed: `schema.yml` documents a `customers.total_order_amount` column that doesn't exist. The model outputs `customer_lifetime_value` instead.
