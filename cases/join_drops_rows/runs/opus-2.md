claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 34 s | USD 0.136 | 2026-10-14 | project f769fdf | prompt 569291640629

## 1. Root cause

**The bad data is in `seeds/raw_payments.csv`. 13 payment rows have an `order_id` that matches no row in `seeds/raw_orders.csv`.**

The numbers line up exactly:

| | amount |
|---|---|
| `sum(stg_payments.amount)` (matches settlement) | 1672.00 |
| `sum(orders.amount)` / `sum(region_revenue.revenue)` | 1532.00 |
| Difference | **140.00** |
| Your orphan query: 13 payments with no matching order | **140.00** |

Every model loses these payments because of how it joins:

- **`models/orders.sql`** groups payments by `order_id`, then `left join`s them *from* `orders`. Payments with an unknown `order_id` form groups that nothing joins to, so they disappear. Order 25 shows 42.00 because only two of its three payments carry `order_id = 25`. The third payment (16.00) is recorded under a non-existent order id.
- **`models/region_revenue.sql`** uses an inner `join orders`, so it drops the same 140.00.
- **`models/customers.sql`** is affected too. Orphan payments roll up into a `customer_id IS NULL` group in `customer_payments`, which the final `left join` from `customers` discards. `customer_lifetime_value` is understated as well.
- **`models/daily_revenue.sql`** is built from `orders`, so it inherits the gap.

Finance says 1672.00 is the settlement *for these orders*, so the payments are real. Their foreign key is what's wrong; the orders are not missing. I don't have the seed CSVs, so I can't tell you which 13 rows or what the correct ids are. This query lists them:

```sql
select p.*
from stg_payments p
left join stg_orders o using (order_id)
where o.order_id is null
order by p.order_id, p.payment_id;
```

Look for a pattern, such as a digit typo or an offset like 125 for 25, and check the rows against the processor's settlement file. One of them should be a 16.00 payment belonging to order 25.

Every test passed because none of them checks this relationship. `stg_payments` only has `unique`/`not_null` on `payment_id` and `accepted_values` on `payment_method`. `orders.amount` is `not_null` and passes as long as each order has at least one matched payment. `region_revenue` only tests its own grain.

**Not the cause:** the cents conversion is fine. `raw_payments.amount` is `INTEGER`, but DuckDB's `/` does float division, and the staging total of 1672.00 already matches settlement.

## 2. Fix

**Data fix (the real one):** correct the `order_id` on the 13 rows in `seeds/raw_payments.csv`, using the processor's payment → order mapping. Don't infer it from the amounts. Then run `dbt build` again. Afterwards `sum(orders.amount)`, `sum(region_revenue.revenue)` and `sum(daily_revenue.revenue)` should all be 1672.00, and order 25 should be 58.00.

`daily_revenue` is incremental and only rebuilds the last 3 days. If the corrected payments belong to older orders, run a full refresh once:

```bash
dbt seed --select raw_payments
dbt build --select raw_payments+ --full-refresh
```

**Code hardening (optional):** no SQL change can recover the right order ids. What you can do is make orphans visible instead of silently dropped, for example with a model for finance to reconcile against:

```sql
-- models/staging/stg_payments_unmatched.sql
select p.*
from {{ ref('stg_payments') }} p
left join {{ ref('stg_orders') }} o on p.order_id = o.order_id
where o.order_id is null
```

## 3. Tests that would have caught it

**a) Referential integrity on payments.** This fails today with 13 rows. Add it in `models/staging/schema.yml`:

```yaml
  - name: stg_payments
    columns:
      - name: payment_id
        tests:
          - unique
          - not_null
      - name: order_id
        tests:
          - not_null
          - relationships:
              arguments:
                to: ref('stg_orders')
                field: order_id
      - name: payment_method
        tests:
          - accepted_values:
              arguments:
                values: ['credit_card', 'coupon', 'bank_transfer', 'gift_card']
```

**b) A reconciliation test that no money is lost between staging and marts.** This catches any future join that drops rows, whatever the reason. Add it as `tests/assert_orders_reconcile_to_payments.sql`:

```sql
-- fails if order totals don't add up to total payments
with p as (select coalesce(sum(amount), 0) as amt from {{ ref('stg_payments') }}),
     o as (select coalesce(sum(amount), 0) as amt from {{ ref('orders') }}),
     r as (select coalesce(sum(revenue), 0) as amt from {{ ref('region_revenue') }})
select p.amt as payments, o.amt as orders, r.amt as region_revenue
from p, o, r
where abs(p.amt - o.amt) > 0.005
   or abs(p.amt - r.amt) > 0.005
```

Two smaller gaps came up while reviewing:
- `raw_customer_regions` is described as "one row per customer" but has no `unique` test on `customer_id`. It also has an `updated_at` column, which suggests history rows could appear later. A duplicate there would inflate `region_revenue`.
- In `models/schema.yml`, `customers` documents `total_order_amount`, but the model outputs `customer_lifetime_value`.
