claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 47 s | USD 0.198 | 2026-10-14 | project ad0b16b | prompt 3fdc8e98d7e9

# Diagnosis: payments with a blank `order_id` in `raw_payments`

## 1. Root cause

**The `order_id` column is blank on some rows of `seeds/raw_payments.csv`.** The SQL models are not the problem.

The raw sample shows it directly:

```
│    91 │     NULL │ credit_card    │    900 │
```

`stg_payments` shows the same thing (`payment_id 15 | NULL | bank_transfer | 14.0`). That is 2 of the 10 sampled rows, so this is not a single bad row. Every downstream model loses these payments:

| Model | What happens to a payment with `order_id = NULL` |
|---|---|
| `orders.sql` | `order_payments` groups them into an `order_id IS NULL` bucket. `left join ... on orders.order_id = order_payments.order_id` never matches NULL, so they disappear. |
| `customers.sql` | `payments left join orders` gives `customer_id = NULL`. That group never joins to `customers`, so it disappears. |
| `region_revenue.sql` | The inner `join orders` drops them. |

The arithmetic matches this: 1672 − 1532 = **140.00 of orphaned payments**. Order 25 has three payments totalling 58.00. One of them, payment 28 for 20.00, is linked correctly. The 42.00 shown means a **16.00 payment for order 25 has lost its `order_id`**.

**Why no test failed:**
- `stg_payments.order_id` has no tests at all.
- `orders.amount` passes `not_null` because every order still has at least one linked payment.
- A `relationships` test would not have caught it either, because dbt's `relationships` test ignores NULLs.

**Supporting evidence from `daily_revenue`:** this model sums to 496 + 415 + 622 + 139 = **1672**, which matches settlement, while `orders` gives 1532 for the same order counts per month. The model is incremental and only rebuilds the last 3 days. Its older rows were therefore built before the `order_id`s were blanked. This shows the regression is recent and in the data. Also, **do not `--full-refresh` `daily_revenue` until the seed is fixed**, or it will drop to 1532 and you lose the only correct per-day copy.

To list the affected rows:

```sql
select * from raw_payments where order_id is null order by id;
-- expect sum(amount) = 14000 (cents), including one 1600 row belonging to order 25
```

## 2. Fix

Restore the missing `order_id` values in `seeds/raw_payments.csv` from the upstream source: the processor settlement file or the previous version of the CSV in git. I can't rebuild them reliably from this output, and guessing the links would silently misattribute revenue. You can cross-check the restored values against the stale `daily_revenue` per-date totals: each day's `revenue` should match after the fix.

Then rebuild:

```bash
dbt build --full-refresh -s raw_payments+
```

Expected results: `sum(orders.amount)`, `customers.customer_lifetime_value`, and `region_revenue.revenue` all equal 1672.00, and order 25 shows 58.00.

If the feed can legitimately send unlinked payments, don't let them vanish silently. Expose them, for example with a `stg_payments` test set to `severity: error`, or an "unallocated" row in the reports, so finance can see them.

## 3. Tests that would have caught it

In `models/staging/schema.yml`, add tests to `stg_payments`:

```yaml
      - name: order_id
        tests:
          - not_null
          - relationships:
              arguments:
                to: ref('stg_orders')
                field: order_id
```

`not_null` catches this bug. `relationships` catches the related case of payments pointing at an order that doesn't exist.

Add a reconciliation test, `tests/assert_orders_reconcile_to_payments.sql`, which catches any future join or filter that drops money, whatever the cause:

```sql
-- Fails if order-level revenue does not reconcile to total payments
select
    (select sum(amount) from {{ ref('stg_payments') }}) as payments_total,
    (select sum(amount) from {{ ref('orders') }})       as orders_total
where abs(payments_total - orders_total) > 0.005
```

You could add the same check for `region_revenue` and `customers.customer_lifetime_value`.

## Other gaps worth fixing

- `raw_customer_regions.customer_id` has no `unique` test. It has an `updated_at` column, so a second row for a customer would fan out `region_revenue`. Add `unique`, plus `relationships` to `stg_customers`.
- `raw_payment_methods.payment_method` has no `unique` test. A duplicate row would double `order_payments` amounts.
- `models/schema.yml` documents `total_order_amount` on `customers`, but the model outputs `customer_lifetime_value`.
