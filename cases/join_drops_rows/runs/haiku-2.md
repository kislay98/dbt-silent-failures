claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 27 s | USD 0.004 | 2026-10-14 | project 7d5b525+dirty

## 1. Root cause

**The payments are fine. The orders are incomplete.** 13 payments, totaling 140.00, reference `order_id`s that don't exist in `raw_orders`. The sample query shows this: `stg_payments` has 113 rows and 1672.00, and `orders` has 99 rows and 1532.00. 1672 − 1532 = 140, which is exactly the orphan total.

Where the rows get dropped:

- **`models/orders.sql`**: `order_payments` aggregates every payment by `order_id`, but `final` starts from `orders` and left-joins to it. Payments whose `order_id` has no row in `stg_orders` never reach the output. The model is driven from the wrong side for a payments reconciliation.
- **`models/region_revenue.sql`**: `payments` is inner-joined to `orders`, so the same 140.00 disappears. That's why it matches `orders` (1532.00) and not finance (1672.00).
- **`models/daily_revenue.sql`** and **`models/customers.sql`** inherit the gap, since both are built on `orders`/payments joined to orders.

**Why the tests pass:** nothing checks that `stg_payments.order_id` exists in `stg_orders`. The only relationship test is `orders.customer_id → customers`. `not_null` on `orders.amount` only checks for NULLs, and the orphan payments never produce a NULL, so the gap is invisible to the test suite.

**Order 25 (42.00 vs 58.00) is not explained by the material provided.** Its amount is `sum(amount)` over all payments with `order_id = 25`, so the join logic should not drop 16.00 for it. Either those payments are attached to a different `order_id` in the raw data, or there's a data problem I can't see here. I haven't run anything; this is from reading the files and output. The first step is to inspect the rows (see below).

## 2. Fix

The correct fix is at the source: `raw_orders` is missing orders that payments reference. Confirm first:

```sql
select p.order_id, count(*) as payments, sum(p.amount) / 100 as dollars
from raw_payments p
where p.order_id not in (select id from raw_orders)
group by p.order_id
order by p.order_id;
```

If those IDs are real orders, load them into the `raw_orders` seed. If they're genuinely orphaned (test data, refunds, bad IDs), don't silently drop them. Surface them so finance sees them.

For order 25, run:

```sql
select p.id, p.order_id, p.payment_method, p.amount / 100 as dollars
from raw_payments p
where p.order_id = 25;

select * from raw_orders where id = 25;
```

Compare the three payments against the order row. Raw `amount` is in cents, so divide by 100 when reading it.

## 3. A test that would have caught this

Add a referential test in `models/staging/schema.yml`, matching the `arguments:` style already used in this repo:

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

This fails the build as soon as a payment references a missing order, which is what happened here.

Also add a reconciliation test in `tests/assert_all_payments_reach_orders.sql`:

```sql
select p.payment_id, p.order_id, p.amount
from {{ ref('stg_payments') }} p
left join {{ ref('stg_orders') }} o
    on p.order_id = o.order_id
where o.order_id is null
```

A singular test passes when it returns zero rows, so this fails whenever any payment is unmatched. The same check would catch a payments-to-orders total mismatch.

**Minor drift:** `models/schema.yml` documents `total_order_amount` and `completed_lifetime_value` for `customers`, but `customers.sql` outputs `customer_lifetime_value` and `completed_lifetime_value`. `total_order_amount` doesn't exist. This doesn't affect the numbers, but it's worth fixing with the rest.
