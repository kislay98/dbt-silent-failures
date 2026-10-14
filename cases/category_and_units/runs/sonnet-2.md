claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 42 s | USD 0.093 | 2026-10-14 | project 8ee5734 | prompt d0c0d63b3bb2

## 1. Root cause

Two things combine, and I'm inferring both from the outputs. I can't see the seed data itself.

**a) `delivered` is never treated as completed** (`models/orders.sql`, `models/customers.sql`, `models/schema.yml`, `models/staging/schema.yml`).
- `customers.sql` hard-codes `orders.status = 'completed'`. Nothing in `orders.sql` or `daily_revenue.sql` defines "completed" at all.
- `delivered` first appears in March (orders 66-76, none earlier). It was added to both `accepted_values` lists, so the tests pass, but no model logic was updated.
- The numbers reconcile with finance:
  - 967 = 424 + 400 + 143, the Jan-Mar `completed` rows, which is exactly `customers.completed_lifetime_value`.
  - 279 - 143 = 136 = March `delivered` revenue, and 1103 - 967 = 136, so all the missing revenue sits in March.

**b) Unit error in `raw_payments.amount`** (the ingestion-loaded seed, which `stg_payments.sql` divides by 100).
- Some coupon payments were loaded in dollars rather than cents. After the `/100` they show up as 0.02, 0.23, 0.07 and 0.24 on orders 76, 81, 86, 92, 94 and 95.
- Order 76 is `delivered` and shows 0.02. If it is really $2.00, March delivered is 134.02 - 0.02 + 2.00 = **136.00**. That closes the gap to finance's 279.00 exactly.
- The same error understates `amount` and `coupon_amount` on the other affected orders, though that doesn't touch completed revenue.

Everything else ties out. The `orders` total of 1612.60 equals `customers.customer_lifetime_value`, so there is no join fan-out from `raw_payment_methods`.

## 2. Fix

**Data (source/ingestion):** reload the affected coupon rows in cents, e.g. 2 → 200 for order 76 and 23 → 2300 for order 86. Then correct the ingestion job's unit handling. Don't patch this in staging with a "small means dollars" heuristic.

**Model:** define completed once, in `orders`, and reuse it.

```sql
-- models/orders.sql, in final
orders.status,
orders.status in ('completed', 'delivered') as is_completed,
```

```sql
-- models/customers.sql
customer_orders as (... unchanged ...),

customer_payments as (

    select
        orders.customer_id,
        sum(amount) as total_amount,
        sum(case when orders.is_completed then amount else 0 end) as completed_amount

    from payments
    left join orders on payments.order_id = orders.order_id   -- ref('orders') instead of stg_orders
    group by orders.customer_id
)
```

Switch the `orders` CTE in `customers.sql` to `ref('orders')`, or add the flag to `stg_orders.sql` instead. Please confirm with finance that `delivered` should count as completed. The reconciliation says it does, but it's a business rule.

`daily_revenue` only rebuilds the last 3 days, so the corrected March data won't be picked up. After the fix, run `dbt build --full-refresh -s daily_revenue`, or `dbt build --full-refresh` for everything.

## 3. Tests

**Unit sanity on the raw feed** (`tests/assert_payment_amounts_in_cents.sql`). In this data every nonzero payment is at least a dollar, so sub-dollar amounts indicate dollars loaded as cents:

```sql
select payment_id, order_id, payment_method, amount
from {{ ref('raw_payments') }}
where amount > 0 and amount < 100
```

Set it to `severity: warn` if genuine sub-dollar payments are possible.

**Reconciliation between models** (`tests/assert_completed_revenue_reconciles.sql`). The log shows this would have flagged the problem as soon as the definitions drifted:

```sql
with o as (select sum(amount) as v from {{ ref('orders') }} where is_completed),
     c as (select sum(completed_lifetime_value) as v from {{ ref('customers') }})
select o.v, c.v from o, c where abs(o.v - c.v) > 0.005
```

**Status coverage.** Replace the `accepted_values` lists with a status-mapping seed (status → `is_completed`) and add `relationships` from `orders.status` to it. A new status then fails until someone classifies it, instead of being waved through.
