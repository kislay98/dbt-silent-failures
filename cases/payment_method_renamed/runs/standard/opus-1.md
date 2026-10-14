claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 23 s | USD 0.173 | 2026-10-14 | project ad0b16b | prompt 57278f2875bf

## 1. Root cause

The ingestion job changed the shape of `raw_payments`. The table has a new column, `method`. For the later payments, the method is written only to `method`, and `payment_method` is left NULL:

| id | order_id | payment_method | method |
|---|---|---|---|
| 28 | 25 | bank_transfer | bank_transfer |
| 57 | 51 | credit_card | credit_card |
| 74 | 65 | **NULL** | credit_card |
| 90 | 79 | **NULL** | credit_card |
| 93 | 81 | **NULL** | coupon |
| 112 | 98 | **NULL** | bank_transfer |

The cutover falls around order 57 (2018-03-01). That is why January and February are fine and March and April are not.

The bug passes through the project like this:

- **`models/staging/stg_payments.sql`** still reads only `payment_method`, so `stg_payments.payment_method` is NULL for every March and April payment.
- **`models/orders.sql`**:
  - Each `sum(case when payments.payment_method = '…' …)` evaluates to `0` for those rows, so all four method columns show 0.00.
  - `sum(payments.amount)` does not depend on the method, so `amount` stays correct. This is why monthly totals, `daily_revenue` and the settlement still agree.
  - The `left join payment_methods` finds no match on a NULL method, and `coalesce(fee_rate, 0)` turns that into `fees = 0`. March and April fees are also understated, not only the payment mix.
- **Why no test failed:**
  - `accepted_values` on `stg_payments.payment_method` ignores NULLs, so it passed.
  - The `not_null` tests on `orders.*_amount` cannot catch this, because the CASE expressions return 0, not NULL.
  - Nothing checks that the per-method columns add up to `amount`.
  - The seed tests in `schema.yml` never run in the nightly build (32 tests are found but only 28 run), because the raw tables are loaded outside dbt.

## 2. Fix

In `models/staging/stg_payments.sql`, use the new column and fall back to the old one:

```sql
renamed as (

    select
        id as payment_id,
        order_id,
        coalesce(method, payment_method) as payment_method,

        -- `amount` is currently stored in cents, so we convert it to dollars
        amount / 100 as amount

    from source

)
```

`orders` is a full table rebuild, so a normal `dbt build` restores the method breakdown and fees for March and April. No `--full-refresh` is needed. `daily_revenue` does not use the method, so it is unaffected.

Before deploying, run these two checks:

```sql
-- should return 0 rows: the two columns never disagree when both are set
select * from raw_payments
where payment_method is not null and method is not null and payment_method <> method;

-- after the rebuild, March credit card volume should be 334.00 to match the processor statement
select round(sum(credit_card_amount), 2) from orders
where date_trunc('month', order_date) = date '2018-03-01';
```

I couldn't run these myself because I only have the samples you pasted, not the warehouse. If the first query returns rows, ask the ingestion team which column is authoritative before choosing the order inside the `coalesce`. You should also confirm with them whether `payment_method` is deprecated. If it is, you can drop it from the `coalesce` later.

## 3. Tests that would have caught it

In `models/staging/schema.yml`, add `not_null` to `payment_method`. On its own, this test would have failed on the first night after the change:

```yaml
  - name: stg_payments
    columns:
      - name: payment_method
        tests:
          - not_null
          - accepted_values:
              arguments:
                values: ['credit_card', 'coupon', 'bank_transfer', 'gift_card']
          - relationships:
              arguments:
                to: ref('raw_payment_methods')
                field: payment_method
```

Also add a reconciliation test on `orders`. It catches any future way the per-method columns could drift from the total, such as a new method value or a renamed column. Save it as `tests/orders_payment_mix_reconciles.sql`:

```sql
-- every order's per-method amounts must add up to its total amount
select order_id, amount,
       credit_card_amount + coupon_amount + bank_transfer_amount + gift_card_amount as method_total
from {{ ref('orders') }}
where abs(amount - (credit_card_amount + coupon_amount + bank_transfer_amount + gift_card_amount)) > 0.001
```

With the current data, this test would fail on every March and April order that has payments. For example, order 59 has `amount` 28.0 and a method total of 0.

Two smaller items are worth fixing while you're in these files:

- **Seed tests aren't running.** The `raw_*` tables come from ingestion, so the tests under `seeds:` never execute. Declare these tables as `sources` with tests, or run `dbt test -s source:*` / `dbt build` including them.
- **Doc mismatch in `models/schema.yml`.** It documents `customers.total_order_amount`, but the model outputs `customer_lifetime_value`.
