claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 42 s | USD 0.214 | 2026-10-14 | project ad0b16b | prompt 3fdc8e98d7e9

# Diagnosis: payments with no `order_id` are being dropped

## 1. Root cause

**The seed `seeds/raw_payments.csv` has rows where `order_id` is blank.** dbt loads a blank as NULL. Those payments are still in `stg_payments`, but every model that joins on `order_id` silently drops them.

The output you pasted shows this directly:

- `raw_payments` / `stg_payments` contain rows with `order_id = NULL`. In the sample alone, payment **91** (credit_card, 900 cents) and payment **15** (bank_transfer, 1400 cents) have no order.
- `sum(stg_payments.amount)` = 1672 and `sum(orders.amount)` = 1532. The **140.00 gap** is the total of the NULL-`order_id` payments.
- Each model loses them in a different way, which is why every report agrees on the wrong number:
  - `models/orders.sql`: `order_payments` groups by `order_id`, so the orphans form a `NULL` group. The `left join ... on orders.order_id = order_payments.order_id` never matches that group.
  - `models/region_revenue.sql`: the inner `join orders on payments.order_id = orders.order_id` drops them. Total is 1532.
  - `models/customers.sql`: they land in a `customer_id IS NULL` group in `customer_payments`, and nothing joins to it. Total is 1532.
- **Order 25:** it should have three payments totalling 58.00. In the upstream jaffle_shop seed these are 28 (bank_transfer 2000), 29 (credit_card 2200) and 30 (coupon 1600). Only 42.00 reaches `orders`, so the 16.00 payment, almost certainly id 30, has lost its `order_id`.
- **Why the tests pass:** `stg_payments.order_id` has no `not_null` or `relationships` test. The `not_null` tests on `orders.*_amount` still pass because the orphans never reach `orders`. No test compares totals.
- **Evidence this was a recent change to the seed:** `daily_revenue` is incremental and only rebuilds the last 3 days. Its months still sum to **1672** (496 + 415 + 622 + 139), while `orders` gives 458 / 394 / 543 / 137. Its older rows were built while the `order_id`s were still filled in. So `daily_revenue` now contradicts `orders`, and the incremental model is hiding the regression.

## 2. Fix

Fix the data. The SQL is correct when payments carry their order.

1. List the affected rows:
   ```sql
   select id, payment_method, amount from raw_payments where order_id is null order by id;
   ```
2. Restore the `order_id` for each row in `seeds/raw_payments.csv`. Use git history of the CSV or the processor settlement file as the source of truth. The rows should look like this:
   ```csv
   15,13,bank_transfer,1400
   30,25,coupon,1600
   ```
   I recognised those two from the upstream jaffle_shop seed. Check them and the remaining rows (91 and the others making up the 140.00) against your source rather than guessing.
3. Rebuild, with a full refresh so `daily_revenue` is recomputed from the corrected `orders` instead of keeping stale partitions:
   ```bash
   dbt build --full-refresh
   ```
   Afterwards, `sum(orders.amount)`, `sum(region_revenue.revenue)`, `sum(customers.customer_lifetime_value)` and `sum(daily_revenue.revenue)` should all equal 1672.00, and order 25 should show 58.00.

If a payment with no order is ever legitimate (for example unattributed processor credits), don't let it vanish silently. Route it to an explicit "unallocated" bucket instead.

## 3. Tests that would have caught it

In `models/staging/schema.yml`, add tests on `stg_payments.order_id`:

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
        ...
```

Add a reconciliation test, `tests/assert_order_amounts_reconcile_to_payments.sql`, which catches any future join that drops money:

```sql
-- Fails if any model loses payments between staging and the marts
with totals as (

    select
        (select sum(amount) from {{ ref('stg_payments') }}) as payments,
        (select sum(amount) from {{ ref('orders') }}) as orders,
        (select sum(revenue) from {{ ref('region_revenue') }}) as region_revenue,
        (select sum(revenue) from {{ ref('daily_revenue') }}) as daily_revenue

)

select *
from totals
where abs(payments - orders) > 0.005
   or abs(payments - region_revenue) > 0.005
   or abs(payments - daily_revenue) > 0.005
```

With the current data, `not_null_stg_payments_order_id` fails on the orphaned payments. The reconciliation test also fails, because 1672 ≠ 1532 and because `daily_revenue` disagrees with `orders`.
