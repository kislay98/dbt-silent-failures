claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 29 s | USD 0.124 | 2026-10-14 | project f769fdf | prompt 2d1dc6cff9f5

## 1. Root cause

The models are correct. The problem is the data in **`seeds/raw_payments.csv`**: **13 payment rows (140.00 total) have an `order_id` that doesn't exist in `seeds/raw_orders.csv`.** Your last query shows this directly. The gap reconciles exactly: 1672.00 − 140.00 = 1532.00.

Every downstream model drops these orphaned payments, each in its own way:

| Model | How the orphans disappear |
|---|---|
| `orders.sql` | It drives from `stg_orders` and left-joins `order_payments`. Payments grouped under a non-existent `order_id` have nothing to attach to. |
| `region_revenue.sql` | It inner-joins `payments` to `orders`, so orphans are filtered out. |
| `customers.sql` | It left-joins `payments` to `orders`, so orphans get `customer_id = NULL`. That NULL group is then dropped by the `customers`-driven left join. |
| `daily_revenue.sql` | It reads from `orders`, so it inherits the loss. |

Order 25 is one of the victims. The customer paid 58.00 across three payments, but only 42.00 is attributed, so one 16.00 payment row carries a wrong `order_id`.

No test caught this because nothing checks the `stg_payments.order_id → stg_orders.order_id` foreign key. `unique`/`not_null` on `payment_id` and `accepted_values` on `payment_method` all pass with mis-keyed rows. The `not_null` tests on `orders.amount` also pass, because order 25 still has *some* payments.

I can't see the CSV contents, so this query lists the bad rows:

```sql
select p.id as payment_id, p.order_id, p.payment_method, p.amount / 100 as amount
from raw_payments p
left join raw_orders o on p.order_id = o.id
where o.id is null
order by p.order_id, p.id;
```

The result will show which case you're in:
- **Mis-keyed payments.** Examples are typos, off-by-one errors, or a corrupted column, such as an order 25 payment pointing at some other ID. In this case the fix goes in `raw_payments.csv`.
- **Orders missing from `raw_orders.csv`**, for example IDs above 99 that were never exported. In this case the fix goes in `raw_orders.csv`.

## 2. Fix

This is a **data fix**. Correct the 13 `order_id` values in `seeds/raw_payments.csv`, or add the missing orders to `raw_orders.csv`, using the processor or source-system records. Then rerun:

```bash
dbt seed --full-refresh && dbt build --full-refresh
```

`--full-refresh` matters for `daily_revenue`. It is incremental and only rebuilds the last 3 days, so older dates would otherwise keep the understated revenue.

Don't guess at the remapping, and don't "fix" this by switching `orders.sql` to drive from payments. That would make totals tie out but produce orders with no customer or date.

## 3. Tests that would have caught it

In `models/staging/schema.yml`, add a foreign-key test on payments:

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

This would have failed with 13 rows. It runs before `orders` and `region_revenue` build, so `dbt build` would have skipped the downstream models instead of publishing wrong numbers.

As a belt-and-braces check, add a reconciliation test at `tests/assert_orders_reconcile_to_payments.sql`:

```sql
-- Revenue must not be lost between staging payments and the orders mart
select *
from (select sum(amount) as payments_total from {{ ref('stg_payments') }}) p
cross join (select sum(amount) as orders_total from {{ ref('orders') }}) o
where abs(p.payments_total - coalesce(o.orders_total, 0)) > 0.005
```

The same pattern can be applied to `region_revenue`.

**Side gaps worth closing (not the cause here):**
- `raw_customer_regions` is documented as "one row per customer" but has no `unique` test on `customer_id`. It also has an `updated_at` column, which suggests it may accumulate history. If a customer ever gets two rows, `region_revenue` will fan out and double-count. Add `unique` to that seed column.
- `models/schema.yml` documents `customers.total_order_amount`, but the model outputs `customer_lifetime_value`.
