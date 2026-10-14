claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 35 s | USD 0.136 | 2026-10-14 | project f769fdf | prompt 962ccbf68360

# Root cause: `delivered` orders are not counted as completed

The source system started emitting a new order status, `delivered`, in March 2018. The shop admin counts `delivered` as completed revenue. The dbt models only count the literal string `'completed'`.

The arithmetic matches exactly, from your `orders` sample:

| | `completed` | `delivered` | sum | admin |
|---|---|---|---|---|
| March | 143.00 | 136.00 | **279.00** | 279.00 |
| Jan–Mar | 424 + 400 + 143 = 967.00 | 136.00 | **1103.00** | 1103.00 |

`customers.completed_lifetime_value` sums to 967.00, which equals `completed` with nothing from `delivered`. Total revenue still matches because `delivered` orders have correct amounts. They are only misclassified when revenue is filtered by status. There are no `delivered` rows before March, so this is a change in the data, not a long-standing bug.

## Files involved

- **`raw_orders.status` (data):** contains `'delivered'` from March 2018 onward. In the admin this is a completed order.
- **`models/customers.sql`, `customer_payments` CTE:** `case when orders.status = 'completed'` hardcodes a single value. All 8 delivered orders (136.00) fall into the `else 0` branch. Customers 19 and 36 in your sample (1 order each, `completed_lifetime_value = 0`) are probably delivered-order customers.
- **`models/schema.yml` and `models/staging/schema.yml`:** the `accepted_values` tests on `status` were widened to include `'delivered'`. That kept `dbt build` green, but nobody updated the logic that interprets status. This is why every test passes.

I don't have the seed data or the project history, so this rests on the exact numeric match. Before shipping, it's worth a quick check that nothing else is going on:

```sql
select status, min(order_date), max(order_date), count(*) from stg_orders group by 1;
```

## 2. Fix

Define "counts as completed revenue" once, in staging, and use that everywhere instead of comparing strings in each mart. Don't rewrite `status` itself, because `delivered` is a real, distinct state.

`models/staging/stg_orders.sql`:
```sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,
        ordered_at,
        cast(ordered_at as date) as order_date,
        status,
        -- the shop admin books both statuses as completed revenue
        status in ('completed', 'delivered') as is_completed

    from source

)
```

`models/customers.sql`:
```sql
        sum(case when orders.is_completed then amount else 0 end) as completed_amount
```

`models/orders.sql`: also expose the flag (`orders.is_completed,` in `final`), so finance can query `where is_completed` instead of `status = 'completed'`. Their own March query (143.00) has the same bug.

Also update the `orders_status` doc block to describe `delivered`. Before merging, confirm with finance or the shop team that `delivered` really means "done, revenue recognised." If it can still turn into `return_pending` or `returned`, that affects how they want to report it.

After `dbt build`, `sum(completed_lifetime_value)` should be 1103.00, and March completed in `orders` should be 279.00.

## 3. Tests that would have caught this

The real gap is that a new status could be *accepted* without being *classified*. Make classification mandatory, and reconcile the marts against each other.

**a) A status → revenue mapping that every status must appear in.** Replace the hardcoded `in (...)` with a seed, `seeds/order_status_mapping.csv`:
```csv
status,is_completed
placed,false
shipped,false
completed,true
delivered,true
return_pending,false
returned,false
```
Join it in `stg_orders` to derive `is_completed`, and replace both `accepted_values` tests with:
```yaml
      - name: status
        tests:
          - not_null
          - relationships:
              arguments:
                to: ref('order_status_mapping')
                field: status
```
Now when the source adds a status, the build fails until someone decides whether it counts as revenue. Adding it to a list without that decision is no longer possible.

**b) A reconciliation test**, `tests/assert_completed_revenue_reconciles.sql`:
```sql
-- completed revenue must agree between customers and orders
with c as (select coalesce(sum(completed_lifetime_value), 0) as v from {{ ref('customers') }}),
     o as (select coalesce(sum(amount), 0) as v from {{ ref('orders') }} where is_completed)
select c.v as customers_completed, o.v as orders_completed
from c, o
where abs(c.v - o.v) > 0.01
```
This guards against the two models drifting apart again. If you can get a monthly completed-revenue export from the shop admin, load it as a seed and compare against that too. That is the only test that checks the business definition rather than internal consistency.

## Unrelated issues I noticed

These are not part of this bug:
- **Possible double-counting in `region_revenue`.** `raw_customer_regions` has an `updated_at` column, which suggests multiple rows per customer. `region_revenue` joins on `customer_id` with no dedup, and the seed has no `unique` test on `customer_id`. If a customer ever has two region rows, their revenue is counted twice. Add `unique` to the seed's `customer_id` or take the latest row per customer.
- **Doc/column name mismatch in `schema.yml`.** It documents `customers.total_order_amount`, but the model outputs `customer_lifetime_value`.
