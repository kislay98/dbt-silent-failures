claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 40 s | USD 0.007 | 2026-10-15 | project ad0b16b | prompt 59943c0b3f30

## 1. Root cause

**The ingestion job changed the payment-method column in `raw_payments`, and `stg_payments.sql` still reads the old column.**

- `raw_payments` now has both `payment_method` and `method`. In the sample, `payment_method` is NULL on March and April payments (for example ids 97, 98, 79, 85, 57, 71). The value is in `method` instead (`bank_transfer`, `credit_card`). January and February rows still populate `payment_method`.
- `models/staging/stg_payments.sql` selects `payment_method` and ignores `method`, so `stg_payments.payment_method` is NULL for March and April.
- `models/orders.sql` matches on that NULL in two places:
  - The per-method `case when payments.payment_method = '...'` never matches, so every `*_amount` column is 0.
  - The left join to `raw_payment_methods` never matches, so `fee_rate` falls back to `coalesce(..., 0)` and `fees` is 0.
- `amount` is `sum(payments.amount)` with no method filter, so `amount` is still correct. That is why the month total is 595.27 while the method columns show 0.00. The coupon ledger gap (45.00 vs 0) is the same bug, since the coupon payments are the NULL-method rows.

**Why every test passed:**
- `accepted_values` on `stg_payments.payment_method` ignores NULLs (`NULL NOT IN (...)` is not a failure), so the NULL rows passed.
- Nothing tests `stg_payments.payment_method` for `not_null`.
- The `not_null` tests on `orders.*_amount` pass because the `else 0` in the `case` returns 0, not NULL.

**One gap I could not explain from the output:** `daily_revenue` (incremental, sums `orders.amount`) shows March at 622.00 and April at 139.00, which match the processor settlement. `orders` shows 595.27 and 106.33. Method is not involved in `amount`, so the NULL method does not explain the 26.73 difference in March or the 32.67 in April. Possible causes:
- `daily_revenue` is stale. Incremental runs only rebuild the last three days, so older March/April rows may come from an earlier load. Check with `dbt run --select daily_revenue --full-refresh` and compare.
- Some payments belong to orders missing from `stg_orders`, or their amounts changed after the initial load.

Run `select sum(p.amount) from stg_payments p join stg_orders o using (order_id) where date_trunc('month', o.order_date) = '2018-03-01'` and compare it with `sum(orders.amount)` for March. That tells you whether the gap is in the join or in staging. I only had the pasted output, so I have not confirmed this.

## 2. Fix

**`models/staging/stg_payments.sql`**: read the new column, falling back to the old one:

```sql
renamed as (

    select
        id as payment_id,
        order_id,

        -- the loader renamed payment_method to method; payment_method is NULL for March onwards
        coalesce(payment_method, method) as payment_method,

        -- `amount` is currently stored in cents, so we convert it to dollars
        amount / 100 as amount

    from source

)
```

Before merging, confirm the two columns never disagree where both are populated:

```sql
select count(*) from raw_payments
where payment_method is not null and method is not null and payment_method <> method;
-- expect 0
```

Also check that `method` only contains the four values in `raw_payment_methods`. If the loader ever drops `payment_method`, the fallback keeps working.

After the fix, `dbt build` should give March and April a method split that sums to `amount`, a non-zero `fees`, and a coupon total near the ledger's 45.00 and 33.00. Confirm the coupon numbers against the ledger yourself, since I could not run the model.

Then resolve the 26.73 / 32.67 gap from the checks above. Once the staging fix is in, rerun `daily_revenue` with `--full-refresh` so stale incremental rows are rebuilt.

## 3. Tests that would have caught this

**a) Not-null on the column that broke.** Add to `models/staging/schema.yml`:

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

**b) A singular test that the method split adds up to the order total.** Create `jaffle_shop/tests/orders_payment_split_sums_to_amount.sql`:

```sql
-- every order's per-method payment amounts must add up to its total;
-- a payment with an unmatched method silently drops out of the split
select
    order_id,
    amount,
    credit_card_amount + coupon_amount + bank_transfer_amount + gift_card_amount as split_total
from {{ ref('orders') }}
where abs(coalesce(amount, 0) - (credit_card_amount + coupon_amount + bank_transfer_amount + gift_card_amount)) > 0.005
```

With the bug present, every March and April order with a payment returns a row, because its `amount` is positive and its split is 0. Test (b) is the one that catches the method-split failure directly. Test (a) catches the NULLs at the source.
