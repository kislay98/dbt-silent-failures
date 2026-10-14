claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 43 s | USD 0.008 | 2026-10-14 | project ad0b16b | prompt 3fdc8e98d7e9

## 1. Root cause

**Where:** `models/orders.sql`, in the `order_payments` CTE and the final left join. `customers.sql` and `region_revenue.sql` inherit the loss because they build on `orders` or repeat the same payments-to-orders join.

**What's wrong in the data:** `raw_payments` contains payments that can't be attributed to any order. The sample shows at least one with `order_id` NULL (`id 91`, 900 cents, credit_card). `stg_payments` keeps these rows, which is why `sum(stg_payments.amount)` is 1672. But `orders.sql` groups payments by `order_id` and then joins the result onto `orders`. A payment with NULL or an unknown `order_id` never reaches an order, so its amount disappears without an error.

**Evidence from the outputs:**
- `region_revenue` sums to exactly 1532.00 (75 + 297 + 285 + 86 + 130 + 377 + 282), the same as `orders`. `customers.lifetime_value` also sums to 1532.00. Every downstream total matches `orders`, so the loss happens at `orders`.
- 1672.00 − 1532.00 = 140.00 is the amount of unattributed payments. The samples show only 10 of 113 payments, so I can't confirm the full 140.00 breakdown from them.
- Order 25 is short by 16.00. The orders model can only drop a payment through its `order_id`, so the likely explanation is that one of order 25's three payments is stored with a NULL or different `order_id` in `raw_payments`. This is a hypothesis until the query below confirms it.

**Why every test passes:**
- `stg_payments.order_id` has no tests at all.
- `orders.amount` is `not_null`, which passes because every order has at least one payment. The problem is payments with no order, not orders with no payments.
- `relationships` tests on `orders.customer_id` don't cover payments, and no test reconciles payment totals to order totals.

**Verify before changing anything:**
```sql
select count(*), round(sum(amount), 2)
from stg_payments
where order_id is null
   or order_id not in (select order_id from stg_orders);

select * from stg_payments where order_id = 25 or order_id is null;
```
If the hypothesis holds, the first query returns a sum of 140.00.

## 2. Fix

**Data (the real fix):** Get the correct `order_id` for the unattributed payments from the processor feed and correct them in `raw_payments`. Don't infer mappings from amounts or dates. For order 25, the missing 16.00 should show up once its payment is re-keyed.

**Model (so the gap can't hide again):** Surface unattributed payments explicitly instead of dropping them. Add `models/unattributed_payments.sql`:

```sql
select
    payment_id,
    order_id,
    payment_method,
    amount
from {{ ref('stg_payments') }}
where order_id is null
   or order_id not in (select order_id from {{ ref('stg_orders') }})
```

Leave `orders.sql` as it is. Its totals are correct for the orders it covers, and the reconciliation below makes the remainder visible.

## 3. Tests that would have caught this

Add to `models/staging/schema.yml`:

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

`relationships` ignores NULLs, so the explicit `not_null` is required to catch NULL `order_id`s.

Add a singular test, `tests/payments_reconcile_to_orders.sql`, that fails if payments don't add up:

```sql
select
    p.paid,
    o.attributed,
    u.unattributed
from (select sum(amount) as paid from {{ ref('stg_payments') }}) p
cross join (select sum(amount) as attributed from {{ ref('orders') }}) o
cross join (select coalesce(sum(amount), 0) as unattributed from {{ ref('unattributed_payments') }}) u
where abs(p.paid - (o.attributed + u.unattributed)) > 0.005
```

Against the current data, both new tests fail (1672.00 vs 1532.00 + unattributed). That failure is the point: it surfaces the 140.00 that the existing suite missed.
