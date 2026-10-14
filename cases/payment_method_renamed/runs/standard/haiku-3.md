claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 38 s | USD 0.007 | 2026-10-14 | project ad0b16b | prompt 57278f2875bf

## 1. Root cause

**`models/staging/stg_payments.sql`** reads only `payment_method`, and in the current raw data that column is NULL for a large share of payments. The `method` column holds the value for those rows.

- `raw_payments` has both `payment_method` and `method`, which suggests the ingestion job moved the field to `method` (or started populating it) and stopped filling `payment_method` for newer rows. I can't see the ingestion job, so confirm this.
- Where `payment_method` is NULL, the `case when payments.payment_method = '...'` checks in **`models/orders.sql`** never match, so every per-method column is 0 for those payments. `total_amount` still sums `amount` for all payments, so `amount` (622.00) is right while the breakdown is empty.
- The fee join in `orders.sql` also depends on `payment_method`. NULL there means `coalesce(fee_rate, 0)` gives 0, which is why March fees are 0.0.
- March and April are zero across all four methods, while January and February are populated. That points to the switch happening around the start of March. April is also affected, but the finance report only flagged March.

**Why every test passed:**

- `accepted_values` on `stg_payments.payment_method` (**`models/staging/schema.yml`**) skips NULLs, so the NULL rows never fail it. There is no `not_null` test on that column.
- The `not_null` tests on `orders.credit_card_amount` and the other method columns pass because the `case ... else 0` never produces NULL. They can't detect a value that is wrong but not null.
- Nothing checks that the per-method amounts add up to `amount`.

The `orders.amount` total, and therefore `daily_revenue` and the settlement match, is unaffected because it never uses `payment_method`.

**Verify before shipping** (I'm inferring this from the outputs and haven't run these):

```sql
-- NULL payment_method by month; expect March and April to be NULL-heavy
select date_trunc('month', ordered_at) ... -- or join to raw_orders
-- card volume the processor should match (raw amounts are cents)
select sum(amount) / 100 from raw_payments where method = 'credit_card' and <March>;   -- expect 334.00
-- the two columns must agree wherever both are populated
select count(*) from raw_payments where payment_method is not null and payment_method <> method;
-- values outside the four known methods (case, whitespace)
select distinct method from raw_payments;
```

## 2. Fix

Fix it in staging, not in the raw data. The ingestion job reloads the raw tables nightly, so a one-off `UPDATE raw_payments` would be overwritten.

**`models/staging/stg_payments.sql`:**

```sql
renamed as (

    select
        id as payment_id,
        order_id,
        -- the feed now sends the method in `method`; `payment_method` is NULL on newer rows
        coalesce(payment_method, method) as payment_method,

        -- `amount` is currently stored in cents, so we convert it to dollars
        amount / 100 as amount

    from source

)
```

Use `coalesce(method, payment_method)` instead if the verification query shows `method` is the more reliable column. Either choice works while the two agree on populated rows, which the sample suggests.

No change is needed in `orders.sql`. After the fix, rebuild the affected graph:

```
dbt build --select stg_payments+
```

`orders` gets the correct breakdown and fees. `customers`, `region_revenue` and `daily_revenue` don't change in value, since they use `amount` and not the method.

## 3. Tests that would have caught it

**a) Not-null on the staging method column.** This fails at the staging layer, before anything downstream runs. Add it to `models/staging/schema.yml`:

```yaml
  - name: stg_payments
    columns:
      - name: payment_id
        tests:
          - unique
          - not_null
      - name: payment_method
        tests:
          - not_null
          - accepted_values:
              arguments:
                values: ['credit_card', 'coupon', 'bank_transfer', 'gift_card']
```

**b) Reconciliation of the breakdown against the order total.** This catches the symptom wherever it comes from. Add `tests/assert_order_payment_breakdown_sums_to_amount.sql`:

```sql
-- the per-method amounts must add up to the order total; returns the offending orders
select
    order_id,
    amount,
    methods_total
from (
    select
        order_id,
        amount,
        credit_card_amount + coupon_amount + bank_transfer_amount + gift_card_amount as methods_total
    from {{ ref('orders') }}
)
where abs(amount - methods_total) > 0.005
```

Under the current data this returns every March and April order, so the nightly build would have failed instead of passing.
