claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 33 s | USD 0.104 | 2026-10-14 | project ad0b16b | prompt 3fdc8e98d7e9

## 1. Root cause

I can't see the seed data, so this is inferred from the outputs. The 140.00 gap (1672 − 1532) has two parts that interact.

**a) Orphaned payments in `seeds/raw_payments.csv`, surfaced in `stg_payments.sql`.**
- Some payment rows have `order_id = NULL`. Examples are raw id 91 (credit_card, 900) and stg payment_id 15 (bank_transfer, 14.00).
- Every downstream model joins on `order_id`, so those rows silently vanish:
  - `orders.sql` left-joins `orders` to `order_payments`, so the payment never lands on any order. That is why order 25 shows 42.00 instead of 58.00: one of its three payments (about 16.00) has a NULL or non-matching `order_id`.
  - `region_revenue.sql` uses an inner `payments join orders`, which drops the rows outright.
  - `customers.sql` puts them under a `NULL` customer group, which is never joined back to a customer.
- Result: `orders`, `customers` and `region_revenue` all total 1532.00, while `sum(stg_payments.amount)` is 1672.00.
- No test covers this. `stg_payments` has no `not_null` or `relationships` test on `order_id`, and the `relationships` test would skip NULLs anyway.

**b) `daily_revenue.sql` is stale and masks the problem.**
- Its monthly totals sum to 496 + 415 + 622 + 139 = **1672.00**, not 1532.00, even though its order counts match. It reads from `orders`, so it should total the same as `orders`.
- The cause is the incremental config. It only deletes and reinserts dates within 3 days of `max(order_date)`. Every older date keeps the values from an earlier run, from before the `order_id`s went NULL (the earlier run, or the data it ran against, must have had them populated).
- So the same project now reports two different revenue numbers. The "payments can land late" comment doesn't match the logic either: the window is relative to the order date, not to when the payment arrived.

## 2. Fix

1. **Fix the data at the source.** Get the correct `order_id` for the NULL rows from the payments feed, or from the processor settlement, and repair `raw_payments`. I can't derive the mapping from the files shown. To list the affected rows:
   ```sql
   select p.*
   from stg_payments p
   left join stg_orders o on p.order_id = o.order_id
   where o.order_id is null;
   ```
   The amounts should sum to 140.00.
2. **Rebuild the incremental model**, since it holds stale history:
   `dbt build --full-refresh -s daily_revenue+`
3. **Make `daily_revenue` safe.** With 99 orders it doesn't need to be incremental, so use `materialized='table'`. If it must stay incremental, key the lookback on a payment-loaded timestamp rather than `order_date`.
4. **Make `region_revenue` and `customers` loud instead of lossy.** Either fail on orphans (the tests below) or route them to an explicit `unassigned` bucket, so they can't disappear.

## 3. Tests

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

Add a reconciliation test, `tests/assert_revenue_reconciles.sql`. It returns rows (fails) if any layer disagrees with the source:
```sql
with src as (select sum(amount) as t from {{ ref('stg_payments') }}),
     o   as (select sum(amount) as t from {{ ref('orders') }}),
     d   as (select sum(revenue) as t from {{ ref('daily_revenue') }}),
     r   as (select sum(revenue) as t from {{ ref('region_revenue') }}),
     c   as (select sum(customer_lifetime_value) as t from {{ ref('customers') }})
select src.t as source, o.t as orders, d.t as daily, r.t as region, c.t as customers
from src, o, d, r, c
where abs(src.t - o.t) > 0.005
   or abs(src.t - d.t) > 0.005
   or abs(src.t - r.t) > 0.005
   or abs(src.t - c.t) > 0.005
```

The `not_null` and `relationships` tests catch the orphans at the source, and the reconciliation test catches both the dropped rows and the stale `daily_revenue`. Before the fix, `dbt build` would have failed on both.
