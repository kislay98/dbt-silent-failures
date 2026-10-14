claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 18 s | USD 0.058 | 2026-10-14 | project bef6b69

## 1. Root cause

**File:** `models/staging/stg_payments.sql`, on top of a schema change in the raw `raw_payments` table.

`raw_payments` now has two method columns: the original `payment_method` and a new `method`, which appears at the end of the column list, so it was added later. From March the ingestion job seems to populate only `method` and leave `payment_method` NULL. I can't see the seed data, so confirm this with the query below.

`stg_payments` only reads `payment_method`, so the March payments reach `orders` with a NULL method. In `orders.sql`, each `sum(case when payment_method = 'credit_card' then amount else 0 end)` then returns 0, and only `sum(amount)` still counts them. This matches the output:

- March and April orders (57 onward) show 0.00 for every method but a correct `amount`.
- Total revenue still matches the settlement.
- January and February are fine.

The tests all pass because none of them can catch this:
- `accepted_values` on `stg_payments.payment_method` skips NULLs by design.
- The `not_null` tests on `orders.*_amount` always pass, since the `else 0` branch means those columns are never NULL.
- Nothing checks that the per-method amounts add up to the order total.

April has the same problem. The 139.00 there is also unsplit, so the bad data is not limited to March.

`region_revenue`, `daily_revenue` and `customers` are not involved, because they only use `amount`.

To confirm the cause, run:
```sql
select date_trunc('month', o.order_date) m, p.payment_method, p.method, count(*), sum(p.amount)
from raw_payments p join raw_orders o on o.id = p.order_id
group by all order by 1;
```
Check that `method` holds the same vocabulary as `payment_method` (for example `credit_card`, not `card` or `CREDIT_CARD`). If it doesn't, map the values in the fix below.

## 2. Fix

In `stg_payments.sql`, read from both columns:
```sql
    select
        id as payment_id,
        order_id,
        coalesce(payment_method, method) as payment_method,

        -- `amount` is currently stored in cents, so we convert it to dollars
        amount / 100 as amount

    from source
```
Then run `dbt build` (a full refresh isn't needed for the table models). `daily_revenue` is unaffected, since it only uses `amount`. Also ask the ingestion owner to either backfill `payment_method` or formally retire it. Either way, the staging model should be the one place that handles the rename.

After the fix, March credit card volume should be 334.00, which you can check against the processor's statement.

## 3. Tests that would have caught it

**a) Make the staging test strict.** `accepted_values` ignores NULLs, so pair it with `not_null`:
```yaml
      - name: payment_method
        tests:
          - not_null
          - accepted_values:
              arguments:
                values: ['credit_card', 'coupon', 'bank_transfer', 'gift_card']
```

**b) Check that the method split adds up to the total** (`tests/assert_orders_method_amounts_sum_to_amount.sql`):
```sql
select order_id, amount,
       credit_card_amount + coupon_amount + bank_transfer_amount + gift_card_amount as method_sum
from {{ ref('orders') }}
where abs(coalesce(amount, 0)
          - (credit_card_amount + coupon_amount + bank_transfer_amount + gift_card_amount)) > 0.005
```
This one fails on every order from 57 onward with the current code. It would catch any future unmapped method, such as a new value or a different spelling, not just this NULL case.

**c) Add a source schema check.** Declare `raw_payments` as a source with a freshness or column check, or use `dbt_utils.expression_is_true`, so a new or renamed upstream column is noticed. Seeds are only used here to stand in for the ingestion job.
