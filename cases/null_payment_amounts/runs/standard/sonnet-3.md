claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 24 s | USD 0.091 | 2026-10-14 | project ad0b16b | prompt b1e95fc444b5

## 1. Root cause

**`raw_payments` (loaded by the ingestion job, not by dbt) has NULL `amount` values, and nothing in the project checks for them.**

- The sample shows payment 30 (order 25, coupon) and payment 55 (order 49, credit_card) with `amount = NULL`. Both flow through `stg_payments.sql` (`amount / 100` stays NULL).
- In `orders.sql`, `sum(payments.amount)` and the per-method `sum(case ... end)` silently skip NULLs. Order 25 therefore loses the coupon payment. The 16.00 gap (58.00 vs 42.00) is consistent with that.
- This is why every test passes:
  - `not_null` on `orders.amount` and the method columns can never fail, because `sum` returns NULL only when all rows are NULL, and the `case ... else 0` sums are never NULL.
  - `stg_payments` has no `not_null` test on `amount`.
  - The `raw_*` seed tests don't appear in the build log (28 tests run, 32 found), so they wouldn't have helped anyway.
- `daily_revenue` hides the problem. It is incremental with a 3-day lookback, so older days were computed when the amounts were still populated. Jan/Feb/Mar in `daily_revenue` are 496 + 415 + 622 = 1533, which matches the settlement. `orders` gives 458 + 394 + 616 = 1468. The 65.00 difference is the NULL-amount payments. The dashboard "agrees" only because it is stale, and a `--full-refresh` would silently drop it to 1468.

## 2. Fix

1. **Data:** list the affected rows and reload them from the processor settlement:
   ```sql
   select * from raw_payments where amount is null;
   ```
   The values can't be inferred from this data. For order 25, the coupon payment should presumably be 1600 cents. Then rebuild `orders`, `customers` and `region_revenue`, and run `daily_revenue --full-refresh` to confirm it still reconciles at 1533.00 for Jan–Mar.
2. **Code:** make `stg_payments.sql` fail loudly instead of dropping values. Either keep the NULL and let the test below stop the build, or use the first option as the default:
   ```sql
   amount / 100 as amount   -- leave as is; enforced by the test below
   ```
3. **Ingestion:** find out why the job started writing NULLs, for example a processor feed change or a pending status.

## 3. Tests

In `models/staging/schema.yml`, under `stg_payments`, because the seed-level tests aren't run by this build:
```yaml
      - name: amount
        tests:
          - not_null
```

A reconciliation test would also have caught the drift. Save it as `tests/assert_daily_revenue_matches_orders.sql`:
```sql
-- returns rows (fails) where the incremental table has drifted from orders
select
    o.order_date,
    o.revenue as orders_revenue,
    d.revenue as daily_revenue
from (select order_date, sum(amount) as revenue from {{ ref('orders') }} group by 1) o
full join {{ ref('daily_revenue') }} d using (order_date)
where coalesce(o.revenue, 0) != coalesce(d.revenue, 0)
```
It fails today for January through March (38, 21 and 6 in total). Treat that as expected until the payments are reloaded, and consider a `warn` severity if late-arriving payments inside the 3-day window can legitimately differ.
