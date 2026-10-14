claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 48 s | USD 0.008 | 2026-10-14 | project ad0b16b | prompt 0f146dda0451

## 1. Root cause

**The data:** some coupon payments in `raw_payments` reference `order_id`s that don't exist in `raw_orders`. Their total is 26.73 in March and 32.67 in April.

The arithmetic points to this:

- March: ledger 45.00 − `coupon_amount` 18.27 = **26.73**. Settlement 622.00 − `sum(amount)` 595.27 = **26.73**. The same money is missing from both the coupon column and the order total.
- April: ledger 33.00 − 0.33 = **32.67**. The stale `daily_revenue` April figure (139.00) − `orders` April (106.33) = **32.67**.
- `customers.customer_lifetime_value` and `region_revenue.revenue` both total 1612.60, which equals `sum(orders.amount)`. Those models join payments to orders, so they only count payments that match an order.

**The file that drops them:** `jaffle_shop/models/orders.sql`. `order_payments` is left-joined onto `stg_orders`, so a payment with no matching order is silently discarded. Nothing in `stg_payments` checks that `order_id` resolves.

**The test gap:** `jaffle_shop/models/staging/schema.yml` tests `stg_payments.payment_id` and `payment_method`, but not `order_id`. That's why every test passes.

**Why `daily_revenue` still shows the money:** `jaffle_shop/models/daily_revenue.sql` is incremental and only rebuilds `order_date >= max(order_date) - 3 days`. The March and April rows were written on an earlier run, when those payments still joined to their orders, and they haven't been rebuilt since. So the warehouse currently has two different March totals: 622.00 in `daily_revenue` (correct, matches settlement) and 595.27 in `orders`. The `orders` figure is the wrong one.

**Why the orphans appeared:** the order count is unchanged (35 March orders in both `orders` and `daily_revenue`), so the orders still exist. The payments probably had their `order_id` changed or nulled in the last ingestion load. I can't confirm that from the excerpt. The ingestion job's change history for `raw_payments` is where to look.

**Ruled out:** `stg_payments` divides by 100 correctly (DuckDB `/` returns DOUBLE, and the output shows 19.0 etc.). `raw_payment_methods` has one row per method, so the seed join doesn't duplicate payments. The coupon `case` expression is correct.

To confirm, run this against the raw tables:

```sql
select p.*
from raw_payments p
left join raw_orders o on p.order_id = o.id
where o.id is null;
```

You should see coupon rows summing to 26.73 (March) and 32.67 (April), possibly with a NULL `order_id`.

## 2. Fix

**Data (the real fix):** correct the `order_id` on those payments at the source, or re-ingest them with the right keys. Once they resolve, the existing `orders` logic is correct and no SQL change is needed for the numbers to recover.

**Code (so this can't fail silently again):**

- In `jaffle_shop/models/orders.sql`, keep the left join but make the orphan case visible. Either fail the build (through the test in part 3) or expose an `unmatched_payments` model, so payments are never discarded without a trace.
- In `jaffle_shop/models/daily_revenue.sql`, the 3-day window means history never converges. Run a one-off full refresh to fix the stale March and April rows:
  ```
  dbt build -s daily_revenue --full-refresh
  ```
  Long term, either widen the window or rebuild from `orders` on a schedule, so restated payments propagate.

## 3. Test that would have caught it

Add this to `jaffle_shop/models/staging/schema.yml`. It fails on any payment whose `order_id` doesn't resolve:

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

It would have failed on this data, because the orphan payments have no matching `order_id`.

A reconciliation test catches the symptom that marketing and finance noticed, which is that payments don't add up to orders. Create `jaffle_shop/tests/payments_reconcile_to_orders.sql`:

```sql
with payments as (
    select sum(amount) as total from {{ ref('stg_payments') }}
),

orders as (
    select sum(amount) as total from {{ ref('orders') }}
)

select payments.total as payments_total, orders.total as orders_total
from payments
cross join orders
where abs(payments.total - orders.total) > 0.005
```

A singular test returns failing rows, so this fails when the totals diverge. It would also flag the `daily_revenue` drift if you add the same check per month against `daily_revenue`.
