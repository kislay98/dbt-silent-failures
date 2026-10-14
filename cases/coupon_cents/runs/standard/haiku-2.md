claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 55 s | USD 0.008 | 2026-10-14 | project ad0b16b | prompt 0f146dda0451

## 1. Root cause

The models are not dropping rows on their own. The gap is in the payment-to-order link, and the models silently discard the unlinked payments.

**The evidence:** The missing money is exactly the coupon money, in both months.

| Month | Settlement / daily_revenue | `orders.amount` | Gap | Ledger coupon | `orders.coupon_amount` | Gap |
|---|---|---|---|---|---|---|
| Mar | 622.00 | 595.27 | 26.73 | 45.00 | 18.27 | 26.73 |
| Apr | 139.00 | 106.33 | 32.67 | 33.00 | 0.33 | 32.67 |

The total gap and the coupon gap are identical each month. Credit card, bank transfer, and gift card amounts don't account for any of it.

**Why `orders` loses them:** In `jaffle_shop/models/orders.sql`, `order_payments` aggregates every payment by `order_id`, including payments whose `order_id` has no matching row in `stg_orders`. The `final` CTE then left-joins from `orders` to `order_payments`, so any aggregate with no matching order is never selected. The coupon payments therefore sit in `raw_payments` (which is what the ledger and processor see) but never reach `orders`. `customers.sql` and `region_revenue.sql` drop them the same way, through their joins.

**What is wrong in the data:** I can't see the `raw_payments` rows that make up the gap, so this part is inferred. The most likely cause is coupon rows in `raw_payments` whose `order_id` doesn't exist in `raw_orders` (an orphaned or re-keyed payment from the ingestion job). A less likely alternative is coupon rows with a NULL `amount`, which `sum()` ignores. Confirm with these queries against the warehouse:

```sql
-- orphaned payments: order_id not in raw_orders
select p.id, p.order_id, p.payment_method, p.amount
from raw_payments p
left join raw_orders o on o.id = p.order_id
where o.id is null;

-- NULL amounts
select * from raw_payments where amount is null;
```

If the first query returns the 26.73 (March) and 32.67 (April) coupon rows, the orphan explanation is confirmed.

**Why the build didn't notice:** No test compares payments to orders. The only relationship test is `customers.customer_id → orders`, and nothing checks `payments.order_id → orders.order_id`. `daily_revenue` also disagrees with `orders` for March and April (622.00 vs 595.27, 139.00 vs 106.33), but it's an incremental model that only rebuilds the last three days, so the disagreement is silent. The stale `daily_revenue` values happen to match the settlement, which is a symptom: the data has changed and `orders` reflects the change, but the historical rows in `daily_revenue` do not.

## 2. Fix

**Data (the actual fix):** Correct the coupon rows in the ingestion job. Either load the missing `raw_orders` rows for those `order_id`s, or re-key the payments to the correct orders. Re-run the ingestion, then verify the reconciliation query below returns zero rows.

**Model (so the same thing can't happen silently):**
- In `jaffle_shop/models/daily_revenue.sql`, drop the incremental logic. It's about 69 rows, so a `table` is cheap, and it removes the stale-history problem. Then run `dbt build -s daily_revenue` (or `--full-refresh`) once after the data fix.
- Also fix the documentation drift: `customers.yml` lists a `total_order_amount` column that `customers.sql` doesn't produce.

## 3. Test that would have caught this

Add a reconciliation singular test at `jaffle_shop/tests/assert_payments_reconcile_to_orders.sql`. It returns rows (and so fails) when the totals differ:

```sql
with payments as (
    select sum(amount) as total from {{ ref('stg_payments') }}
),
orders as (
    select sum(amount) as total from {{ ref('orders') }}
)
select payments.total, orders.total
from payments, orders
where abs(payments.total - orders.total) > 0.005
```

Also add a relationship test and a not-null test in `jaffle_shop/models/staging/schema.yml`:

```yaml
  - name: stg_payments
    columns:
      - name: order_id
        tests:
          - relationships:
              arguments:
                to: ref('stg_orders')
                field: order_id
      - name: amount
        tests:
          - not_null
```

The relationship test fails on the orphaned payments directly. The reconciliation test catches any future leak, whatever its cause, and would have flagged March and April in the first nightly run. Run `dbt test` after each change to confirm it fails on the current data before you fix the ingestion.
