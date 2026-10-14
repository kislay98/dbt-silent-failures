claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 34 s | USD 0.006 | 2026-10-14 | project ad0b16b | prompt a85110595fb3

## 1. Root cause

The ingestion job changed the `raw_payments` feed for recent rows. The method is now in a column called `method`, and `payment_method` is NULL. `stg_payments.sql` passes `payment_method` through unchanged, so the NULLs flow downstream into `orders.sql`:

- **`models/staging/stg_payments.sql`**: `payment_method` is selected as-is, so NULL is passed through even though `method` holds the value. The raw sample shows this: payment ids 86, 81, 65 and others have `payment_method` NULL and `method` populated, while the two rows with a populated `payment_method` have low ids.
- **`models/orders.sql`**: `order_payments` computes each per-method column as `sum(case when payments.payment_method = 'credit_card' ...)`. A NULL never equals a string, so every per-method column is 0 for those orders. The `fees` calculation joins `payment_methods` on `payment_method`, finds no match, and coalesces the rate to 0, so fees are 0 too. `amount` is `sum(payments.amount)` with no method filter, so it stays correct. That is why the month totals (708, 146) are right while the splits are 0.

This matches the output. For January, 218 + 90 + 136 + 52 = 496, which equals the total. For March and April, the splits are 0 and the total is 708 and 146.

**Why the tests pass:**
- `accepted_values` on `stg_payments.payment_method` ignores NULLs (`where value not in (...)` is NULL for NULL inputs), so it passes.
- There is no `not_null` on `stg_payments.payment_method`.
- `not_null` on `orders.credit_card_amount` and the others passes because the values are 0, not NULL.
- No test checks that the per-method amounts add up to `amount`.

**To confirm before fixing** (I have not run these; they are the checks that would settle it):
```sql
-- should be 334.00 if the processor is right
select sum(p.amount) / 100.0
from raw_payments p
join raw_orders o on p.order_id = o.id
where p.method = 'credit_card'
  and o.ordered_at >= '2018-03-01' and o.ordered_at < '2018-04-01';

-- should return 0 rows: where both columns are present, do they agree?
select id, payment_method, method from raw_payments
where payment_method is not null and payment_method <> method;

-- how many rows are affected
select count(*) from raw_payments where payment_method is null;
```

## 2. Fix

In `models/staging/stg_payments.sql`, fall back to the new column:

```sql
renamed as (

    select
        id as payment_id,
        order_id,
        -- the feed now sends the method in `method` for newer rows; `payment_method` is NULL there
        coalesce(payment_method, method) as payment_method,

        -- `amount` is currently stored in cents, so we convert it to dollars
        amount / 100 as amount

    from source

)
```

The longer-term fix belongs in the ingestion job: it should populate `payment_method` again, or the rename should be documented and the staging model updated to read `method` only. Check that the two columns agree (query above) before picking which one is authoritative.

Then rebuild the affected chain:

```
dbt build --select stg_payments+
```

`orders` is a table, so it rebuilds fully. Re-run the March and April query. Expect the credit card total for March to be 334.00 and the four splits plus fees to be non-zero.

## 3. A test that would have caught it

Two tests would have failed on this data:

**a) `not_null` on the staging column** (in `models/staging/schema.yml`):
```yaml
  - name: stg_payments
    columns:
      - name: payment_method
        tests:
          - not_null
          - accepted_values:
              arguments:
                values: ['credit_card', 'coupon', 'bank_transfer', 'gift_card']
```
This fails immediately, because `accepted_values` does not catch NULLs.

**b) A reconciliation test on `orders`** that the method splits sum to the total. Add `tests/assert_orders_payment_splits_sum_to_amount.sql`:
```sql
-- every order's per-method amounts must add up to its total amount
select order_id, amount,
       coalesce(credit_card_amount, 0) + coalesce(coupon_amount, 0)
       + coalesce(bank_transfer_amount, 0) + coalesce(gift_card_amount, 0) as split_total
from {{ ref('orders') }}
where abs(coalesce(credit_card_amount, 0) + coalesce(coupon_amount, 0)
          + coalesce(bank_transfer_amount, 0) + coalesce(gift_card_amount, 0)
          - amount) > 0.005
```
This test would have failed on every March and April order, whichever staging issue caused it. It also guards against new payment methods that the model doesn't list.

## Other things I noticed (not the cause of this)

- **`daily_revenue` does not match `orders`** for March and April (622 vs 708, and 139 vs 146). The order counts match, so the difference is in amounts. The incremental model only rebuilds the last three days, so older days don't pick up payments that landed late. This is a separate issue. A `dbt run --select daily_revenue --full-refresh` should fix it, and I would check that the totals match after the refresh.
- **Docs drift:** `customers` documents a `total_order_amount` column that the model doesn't produce; the model has `customer_lifetime_value`. `orders` documents no `payment_method` input, so the mismatch was hard to spot.
