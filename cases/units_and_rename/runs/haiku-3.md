claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 32 s | USD 0.005 | 2026-10-14 | project bef6b69 | prompt aaad08d98757

## 1. Root cause

**`jaffle_shop/models/staging/stg_payments.sql`** reads `payment_method` from `raw_payments`, but for March and April that column is NULL. The values are in the raw table's other column, `method`, which nothing references.

The sample confirms it. Order 58 has two payments with `payment_method = NULL`, and so do orders 76 and 92. Order 56 (February) has `credit_card` populated, so the column is not NULL everywhere. The likely cause is that the ingestion job began writing payment method to `method` for newer loads, but I haven't verified that against the warehouse.

The bug propagates like this:

- **`stg_payments`** passes NULL through for the affected rows.
- **`orders.sql`** computes `sum(case when payment_method = 'credit_card' ...)`. In SQL, `NULL = 'credit_card'` is never true, so every method column is 0 for those payments. The `amount` column is `sum(amount)` with no method filter, which is why it still shows the correct 595.27 and 106.33.
- **The output** therefore shows 0.00 in every method column for March and April.

**Why every test passed:**

- `accepted_values` on `stg_payments.payment_method` ignores NULLs. Its SQL only returns rows where the value is non-null and not in the list, so NULLs pass silently.
- There is no `not_null` test on `payment_method`.
- The `orders` method columns only have `not_null` tests. Zeros satisfy `not_null`.
- Nothing checks that the method columns add up to `amount`.

**A second gap is not explained by this bug.** `amount` comes from all payments, not from the method columns, so the 595.27 total is already independent of `payment_method`. The March gap to settlement (622.00 − 595.27 = 26.73) and the April gap (139.00 − 106.33 = 32.67) have a different cause. The likeliest candidate is payments whose `order_id` has no matching row in `raw_orders`. `orders` starts from `stg_orders`, so the left join drops those payments. I can't confirm this from the inputs given.

## 2. Fix

**Stopgap in dbt** (`jaffle_shop/models/staging/stg_payments.sql`). Before applying it, confirm that each row has exactly one of the two columns populated, and that the values in `method` match the accepted list.

```sql
renamed as (

    select
        id as payment_id,
        order_id,
        -- `payment_method` is NULL for newer loads; those rows carry the method in `method`
        coalesce(payment_method, method) as payment_method,

        -- `amount` is currently stored in cents, so we convert it to dollars
        amount / 100 as amount

    from source

)
```

**Real fix:** correct the ingestion job so `raw_payments` has one populated method column with a consistent name, then remove the `coalesce`.

**Verify before and after** (I haven't run these):

```sql
-- which column carries the method, by month
select date_trunc('month', o.ordered_at) as month,
       p.payment_method is null as pm_null,
       p.method is null         as m_null,
       count(*),
       sum(p.amount) / 100      as dollars
from raw_payments p
join raw_orders o on o.id = p.order_id
group by all
order by 1;

-- payments with no matching order (candidate for the 26.73 / 32.67 gap)
select date_trunc('month', p.ordered_at) ...
```

Once the method columns are fixed, March should show about 45.00 in `coupon` if the coupon ledger matches the data. April should show about 33.00.

## 3. Tests that would have caught this

**a) `not_null` on the staging column** (`jaffle_shop/models/staging/schema.yml`). This fails on the NULL rows immediately.

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

**b) A reconciliation singular test** (`jaffle_shop/tests/orders_methods_sum_to_amount.sql`). This catches the zeroed methods even if the NULLs reappear through a different path. Order 58 would fail it (amount 24.00, methods 0.00).

```sql
-- every order's per-method amounts must add up to its total
select
    order_id,
    amount,
    credit_card_amount + coupon_amount + bank_transfer_amount + gift_card_amount as methods_total
from {{ ref('orders') }}
where abs(coalesce(amount, 0) - (credit_card_amount + coupon_amount + bank_transfer_amount + gift_card_amount)) > 0.005
```

Test (a) alone would have failed the nightly build at the source. Test (b) would have failed it at the output. Either one would have stopped this from reaching finance.

**Side note:** `jaffle_shop/models/schema.yml` documents a `total_order_amount` column on `customers` that the model doesn't produce. It's a docs mismatch, not part of this bug.
