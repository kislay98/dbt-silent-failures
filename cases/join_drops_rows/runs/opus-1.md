claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 25 s | USD 0.115 | 2026-10-14 | project f769fdf | prompt 569291640629

The seeds and warehouse aren't in this directory, so this is based on the files and query output you pasted. I can identify the faulty file and the size of the problem exactly, but not which 13 rows are wrong.

## 1. Root cause

**The `raw_payments` seed (`jaffle_shop/seeds/raw_payments.csv`) has 13 payment rows whose `order_id` does not exist in `raw_orders`.** The model SQL is not the cause.

Your own query shows it:

```
payments with no matching stg_orders row: 13 rows, 140.00
1672.00 (all payments) − 140.00 (orphans) = 1532.00 (orders, region_revenue)
```

How the orphan payments disappear from each model:

- **`models/orders.sql`** starts from `stg_orders` and left-joins `order_payments`. A payment whose `order_id` matches no order never joins to anything, so it vanishes. Order 25 shows 42.00 because only some of its three payments carry `order_id = 25`. The rest (16.00) carry some other id that isn't a real order.
- **`models/region_revenue.sql`** inner-joins `payments` to `orders`, so the same 13 rows are dropped. Its total is 1532.00 too, which rules out the regions join (no fan-out, no loss there).
- **`models/customers.sql`** goes the other way. It does `payments left join orders`, so the orphans are kept but get `customer_id = NULL`. That produces a NULL-customer group that the final join drops. `customer_lifetime_value` is understated in the same way.

The column types are fine: `order_id` is INTEGER on both sides, and `amount` is an INTEGER in cents that `stg_payments` divides by 100. So this is not a type or cast problem. The ids themselves are wrong in the payments feed.

Why every test passed: nothing tests `stg_payments.order_id`. There is no `not_null` and no `relationships` test on it, and no test compares totals between payments and orders.

## 2. Fix

To find the bad rows:

```sql
select p.id, p.order_id, p.payment_method, p.amount
from raw_payments p
left join raw_orders o on p.order_id = o.id
where o.id is null
order by p.order_id, p.id;
```

Then correct `order_id` for those rows in `seeds/raw_payments.csv`, or fix it upstream in the payments extract if the seed is a copy of a feed. Order 25's missing payments (16.00 total) should be among them, and the pattern of the wrong ids (for example an offset or swapped digits) will show you how the other 12 should map. Run `dbt build` again. After that, `sum(orders.amount)`, `region_revenue`, and the orphan query should give 1672.00 / 1672.00 / 0 rows.

Don't "fix" this by changing the joins (for example a full outer join in `orders.sql`). That would make the totals match but leave 140.00 assigned to orders and customers that don't exist.

## 3. Tests that would have caught it

In `models/staging/schema.yml`, under `stg_payments`:

```yaml
      - name: order_id
        tests:
          - not_null
          - relationships:
              arguments:
                to: ref('stg_orders')
                field: order_id
```

With the current data, the `relationships` test fails with 13 rows. That test alone would have caught this.

As a second safety net, add a reconciliation test so any future join that silently loses money also fails, e.g. `tests/assert_orders_reconcile_to_payments.sql`:

```sql
-- returns a row (fails) if order totals drift from payment totals
select
    (select sum(amount) from {{ ref('stg_payments') }}) as payments_total,
    (select sum(amount) from {{ ref('orders') }})       as orders_total
where abs(
    (select sum(amount) from {{ ref('stg_payments') }})
  - (select coalesce(sum(amount), 0) from {{ ref('orders') }})
) > 0.001
```

You could add the same check for `region_revenue.revenue`.
