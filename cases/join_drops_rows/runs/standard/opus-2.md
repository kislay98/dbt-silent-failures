claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 36 s | USD 0.200 | 2026-10-14 | project ad0b16b | prompt 3fdc8e98d7e9

# Root cause: payments with a NULL `order_id` in the `raw_payments` seed

## 1. Root cause

**The bug is in the data, not the SQL.** Some rows in `seeds/raw_payments.csv` have an empty `order_id`, so they load as `NULL`. The sample shows two of them:

| id | order_id | payment_method | amount (cents) |
|---|---|---|---|
| 15 | NULL | bank_transfer | 1400 |
| 91 | NULL | credit_card | 900 |

The sample is only 10 of 113 rows, so there are probably more. Together they must account for the 140.00 gap (1672 − 1532). Order 25's missing 16.00 is one or more of these rows. Only payment 28 (20.00) is still linked to order 25.

**How each model loses these rows:**

- **`stg_payments.sql`** passes them through. That is why `sum(stg_payments.amount)` is 1672 and matches settlement.
- **`orders.sql`** groups `order_payments` by `payments.order_id`, which creates a `NULL` group. It then does `stg_orders left join order_payments on order_id`, and `NULL` never matches. Total: 1532.
- **`region_revenue.sql`** uses an inner `join orders on payments.order_id = orders.order_id`, which drops them. Total: 1532.
- **`customers.sql`** puts them in a `NULL` customer group, and the final join from `customers` discards it. Total: 1532.

**Why no test failed:** `models/staging/schema.yml` tests `stg_payments.payment_id` and `payment_method`, but not `order_id`. No test checks `not_null` on it, `relationships` to orders, or that totals reconcile.

**Evidence that this is a recent data regression.** `daily_revenue` is incremental and only rebuilds the last 3 days. Its monthly totals are 496 + 415 + 622 + 139 = **1672**. That is the correct number, preserved from earlier runs when the payments still had their `order_id`. Compared with `orders`, each month differs by +38, +21, +79 and +2, which sums to 140.

## 2. Fix

The `order_id` values can't be derived from anything else in `raw_payments`; the only other columns are `id`, `payment_method` and `amount`. They have to be restored at the source:

1. List the affected rows:
   ```sql
   select * from raw_payments where order_id is null order by id;
   ```
2. Restore the `order_id` values in `seeds/raw_payments.csv`. Use the last good version from version control (`git log -p seeds/raw_payments.csv`) or the processor's settlement file. Order 25 should end up with three payments totaling 5800 cents.
3. Rebuild, including a full refresh of the incremental model so no stale or partial days remain:
   ```bash
   dbt build --full-refresh
   ```
4. Check the result: `sum(orders.amount)`, `sum(region_revenue.revenue)`, `sum(customers.customer_lifetime_value)` and `sum(daily_revenue.revenue)` should all be 1672.00, and order 25 should show 58.00.

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

Add a reconciliation test so that payments can never be dropped silently, whatever the cause. For example, `tests/assert_orders_reconcile_to_payments.sql`:

```sql
-- Fails if any payment amount is lost between staging and the orders mart
with payments as (
    select round(sum(amount), 2) as total from {{ ref('stg_payments') }}
),

orders as (
    select round(sum(amount), 2) as total from {{ ref('orders') }}
)

select payments.total as payments_total, orders.total as orders_total
from payments, orders
where payments.total is distinct from orders.total
```

You can add the same check for `region_revenue.revenue`.

## Smaller issues worth fixing

- **`raw_customer_regions`** has an `updated_at` column but no `unique` test on `customer_id`. A second row for the same customer would double-count revenue in `region_revenue`. Add `unique` there.
- **`models/schema.yml`** documents a `total_order_amount` column on `customers`, but the model outputs `customer_lifetime_value` instead. Rename one so they match.
