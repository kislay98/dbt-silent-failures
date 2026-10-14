claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 25 s | USD 0.004 | 2026-10-14 | project bef6b69 | prompt aaad08d98757

## 1. Root cause

**`jaffle_shop/models/staging/stg_payments.sql`** reads `payment_method` from `raw_payments`, but for the March and April loads that column is NULL. The method values sit in a second column, `raw_payments.method`, which the ingestion job populates. The staging model never reads it.

- `orders.sql` builds each `*_amount` column with `sum(case when payment_method = '<method>' then amount else 0 end)`. A NULL `payment_method` matches no branch, so every method column is 0.00 for those payments.
- `order_payments.total_amount` is `sum(amount)` with no method filter, so the total still includes them. That is why the month totals (595.27, 106.33) look plausible while the breakdown is zero.
- The raw `payment_method` is NULL for the March and April rows in the sample (orders 58, 76, 92). Orders 55 to 62 in January and February have populated methods.

**Why every test passed:** `accepted_values` on `stg_payments.payment_method` ignores NULLs, so NULL rows pass. Nothing in the project asserts that the method breakdown adds up to `amount`, or that `payment_method` is not null.

I could not verify from what you sent:
- Whether `method` is the authoritative column. Confirm with the ingestion owner, and check that `method` and `payment_method` agree on Jan and Feb rows where both are populated.
- Whether `method` uses the same vocabulary (`credit_card`, `coupon`, and so on). The fix below assumes it does.
- The 26.73 gap between the March total (595.27) and settlement (622.00). It is probably not explained by the NULL method alone. Check for March payments whose `order_id` is missing from `stg_orders`, since the `left join` from `orders` drops them, and for payments on other orders in March that are absent from `orders`.

Minor, unrelated to this bug: `customers` in `models/schema.yml` documents `total_order_amount`, which the model does not produce.

## 2. Fix

**Code change**, in `jaffle_shop/models/staging/stg_payments.sql`:

```sql
renamed as (

    select
        id as payment_id,
        order_id,

        -- `payment_method` is NULL in newer loads; the ingestion job writes the method to `method`
        coalesce(payment_method, method) as payment_method,

        -- `amount` is currently stored in cents, so we convert it to dollars
        amount / 100 as amount

    from source

)
```

If `method` turns out to be the only correct column, use `method` directly instead. Either way, the ingestion job should be fixed so the two columns cannot diverge silently.

**Schema change**, in `jaffle_shop/models/staging/schema.yml`, so NULLs fail instead of passing:

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

**Rebuild:** `dbt build --select stg_payments+`. `daily_revenue` and `customers` use the unchanged `amount`, so a `--full-refresh` of the incremental model is not needed. The March and April method columns should then match the coupon ledger (45.00 and 33.00) and the settlement once the 26.73 gap is resolved.

**Data change (if needed):** if `method` is correct and `payment_method` is stale, backfill `raw_payments.payment_method` from `method` in the ingestion job, then rerun the staging and downstream models.

## 3. A test that would have caught this

Add a singular test at `jaffle_shop/tests/assert_payment_methods_sum_to_amount.sql`. It fails if the method breakdown does not add up to the order total, which is the invariant the report depends on:

```sql
select
    order_id,
    amount,
    credit_card_amount + coupon_amount + bank_transfer_amount + gift_card_amount as methods_total
from {{ ref('orders') }}
where abs(amount - (credit_card_amount + coupon_amount + bank_transfer_amount + gift_card_amount)) > 0.005
```

Together with the `not_null` on `stg_payments.payment_method`, this test would have failed on the March and April rows in the first build. The `not_null` catches the cause, and the reconciliation test catches the symptom finance saw, even if a future change reintroduces the problem some other way.
