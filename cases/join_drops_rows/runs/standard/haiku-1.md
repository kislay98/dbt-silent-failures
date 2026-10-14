claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 54 s | USD 0.009 | 2026-10-14 | project ad0b16b | prompt 3fdc8e98d7e9

## 1. Root cause

**The gap is payments that never reach an order, and nothing in the project flags them.** The cause sits in `models/orders.sql`, with the same pattern in `region_revenue.sql` and `customers.sql`.

- `order_payments` groups `stg_payments` by `order_id`, then `orders` left-joins onto it. A payment whose `order_id` is NULL, or doesn't exist in `raw_orders`, forms a group that never matches an order. Its amount is in `stg_payments` but in no row of `orders`.
- `region_revenue.sql` starts from `payments` and inner-joins to `orders`, so unmatched payments are dropped there too.
- `customers.sql` left-joins `payments` to `orders`, so unmatched payments get a NULL `customer_id` and are dropped at the final join.

The loss is visible in the data:

- `raw_payments` has NULL `order_id` rows: id 91 (900 cents, credit_card) and, in staging, `payment_id` 15 (14.00, bank_transfer). Together that's **23.00** of the **140.00** gap (1672.00 − 1532.00).
- The remaining **117.00** must come from payments with `order_id` values that aren't in `raw_orders`. The samples don't show them, so I can't name them from what's here.
- `order 25` is different. Its three payments total 58.00, but `orders` shows 42.00. The `orders.sql` join can only drop a payment whose `order_id` isn't 25, so the 16.00 payment most likely has the wrong `order_id` in `raw_payments`, which would make it a data-entry problem. I can't confirm that from the samples.

`daily_revenue` is a separate, stale problem. Its revenue sums to exactly 1672.00, which matches the settlement total, so it was built when orders carried these payments. Its incremental filter only rebuilds the last three days, so older rows never get corrected. Its April total (139.00) also differs from `orders` (137.00).

`dbt build` passes because no test covers payment-to-order integrity or reconciles totals. `not_null` and `relationships` are only declared on `orders.customer_id`, `stg_orders`, and the seeds, never on `stg_payments.order_id`.

To confirm the breakdown:

```sql
select p.payment_id, p.order_id, p.amount
from stg_payments p
left join stg_orders o on p.order_id = o.order_id
where o.order_id is null;

select * from stg_payments where order_id = 25;
```

The first query should sum to 140.00 if the orphans explain the whole gap. The second should show the three payments totalling 58.00, one of which may be keyed to a different order.

## 2. Fix

**Data:** Correct the `order_id` on the NULL and orphan rows in `raw_payments` from the processor feed (for example, the 16.00 payment for order 25). If a payment can't be matched, quarantine it explicitly rather than letting it vanish.

**Code, in `models/staging/schema.yml`:** Make missing links fail the build.

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

**Code, daily_revenue:** Restate the history once with `dbt run -s daily_revenue --full-refresh`. Going forward, either rebuild the whole table or widen the window if payments can land later than three days.

**Docs:** `customers` documents `total_order_amount`, which the model doesn't produce. Remove or fix it.

## 3. Test that would have caught it

Add a singular test at `tests/assert_payments_reconcile_to_orders.sql`:

```sql
with paid as (
    select sum(amount) as amount from {{ ref('stg_payments') }}
),

ordered as (
    select sum(amount) as amount from {{ ref('orders') }}
)

select paid.amount as paid_amount, ordered.amount as ordered_amount
from paid
cross join ordered
where abs(paid.amount - ordered.amount) > 0.005
```

Any mismatch between settled payments and order amounts now fails the build, with the two totals in the output. The `relationships` test on `stg_payments.order_id` above catches the specific orphaned rows.
