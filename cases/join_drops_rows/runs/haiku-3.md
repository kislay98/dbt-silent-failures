claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 27 s | USD 0.004 | 2026-10-14 | project 7d5b525+dirty

## 1. Root cause

**The gap is 13 payments (140.00) whose `order_id` has no matching row in `raw_orders`.** The defect is in the data and the joins that assume every payment has an order, not in the arithmetic.

- `stg_payments.sql` converts cents to dollars correctly. Its total is 1672.00, which matches finance.
- `orders.sql` builds `order_payments` from *all* payments grouped by `order_id`, then left-joins from `stg_orders`. Payments whose `order_id` isn't in `stg_orders` never reach `orders`. 1672.00 − 140.00 = 1532.00, which is exactly what `orders` and `region_revenue` show.
- `daily_revenue` reads from `orders`, so it inherits the same loss.
- `region_revenue.sql` inner-joins `payments` to `orders`, so orphan payments are dropped there too.

The `orders` tests pass because they only check the rows that exist. Nothing checks that `sum(payments)` reconciles to `sum(orders.amount)`, and there is no `relationships` test from `stg_payments.order_id` to `stg_orders.order_id`. The payments `accepted_values` and `unique` tests don't look at foreign keys either.

**Order 25 (42.00 shown, 58.00 paid):** I can't confirm this from the sample. Orders are joined by `order_id`, so the 16.00 gap is most likely a payment that carries a different `order_id` and is therefore counted against another order. That would move money between orders without changing the total, which fits the totals reconciling to 1532. To confirm, run:

```sql
select p.id, p.order_id, p.amount, p.payment_method
from raw_payments p
where p.order_id = 25
   or p.id in (select id from raw_payments where order_id not in (select id from raw_orders));
```

Then check whether one of order 25's payments is among the orphans or is attached to a neighboring order.

## 2. Fix

The orphan payments need a data-level resolution, because the revenue reports are only correct once every payment maps to an order. I can't tell from here whether they belong to missing orders or have bad `order_id`s, so list them first:

```sql
select p.id, p.order_id, p.amount
from raw_payments p
left join raw_orders o on p.order_id = o.id
where o.id is null;
```

- If the orders are missing from `raw_orders`, add them to the seed or source.
- If the `order_id`s are wrong, correct them in `raw_payments`.

Once orphans are resolved, `orders.amount` and `region_revenue.revenue` will both reconcile to 1672.00.

To make sure this fails loudly next time, I'd also make `orders.sql` and the revenue models stop silently dropping rows. Two concrete changes:

- In `schema.yml`, add a `relationships` test on `stg_payments.order_id` → `stg_orders.order_id`.
- Add a singular test `tests/assert_payments_reconcile_to_orders.sql` that returns a row whenever `sum(stg_payments.amount)` ≠ `sum(orders.amount)`.

I haven't changed any files. Tell me whether to make the seed-data fix or only add the guard tests, and I'll make the change and run `dbt build`.

## 3. A test that would have caught this

The relationship test would have caught it directly:

```yaml
  - name: stg_payments
    columns:
      - name: order_id
        tests:
          - not_null
          - relationships:
              arguments:
                to: ref('stg_orders')
                field: order_id
```

Against this data it fails with 13 rows. The reconciliation singular test catches the dollar amount (140.00) even if someone later relaxes the relationship test:

```sql
-- tests/assert_payments_reconcile_to_orders.sql
with payment_total as (
    select sum(amount) as amt from {{ ref('stg_payments') }}
),
order_total as (
    select sum(amount) as amt from {{ ref('orders') }}
)
select p.amt as payments_total, o.amt as orders_total
from payment_total p, order_total o
where abs(p.amt - o.amt) > 0.005
```
