claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 21 s | USD 0.004 | 2026-10-14 | project bef6b69

## 1. Root cause

The March rows in `raw_payments` have a NULL `payment_method`, and the card, coupon, bank transfer, and gift card amounts come out as zero as a result. The payment amounts themselves are fine.

- **`jaffle_shop/models/staging/stg_payments.sql`** selects `payment_method` and passes it through unchanged. The raw table also has a second column, `method`, which the staging model ignores. The raw schema shows both `payment_method` and `method` in `raw_payments`, so the ingestion job likely changed where the method is written. The likely cause is that March's rows have the method in `method` and NULL in `payment_method`. I can't confirm the values from the output alone.
- **`jaffle_shop/models/orders.sql`** pivots on `payment_method = '<name>'`. For a NULL method, every `case` returns `else 0`, so March shows 0.00 in each method column. `sum(amount)` is unaffected, which is why `amount` totals 622.00.

**Why the tests pass:**
- `accepted_values` on `stg_payments.payment_method` (in `staging/schema.yml`) ignores NULLs, so NULL rows pass.
- There is no `not_null` test on `payment_method`.
- The `not_null` tests on `orders.*_amount` pass because the pivot writes 0 instead of NULL.
- Nothing checks that the per-method amounts add up to `amount`.

**Verify before fixing:**

```sql
select method, payment_method, date_trunc('month', ordered_at) as month, count(*) as n, sum(amount) as cents
from raw_payments p
join raw_orders o on o.id = p.order_id
group by all
order by month;
```

If March rows show `payment_method` NULL with real values in `method`, the diagnosis holds. Also check whether the `method` values match the four expected names, for example `credit_card` versus `card` or `CreditCard`. If they don't, the mapping needs a lookup, not a plain `coalesce`.

Downstream impact: `daily_revenue`, `customers` and `region_revenue` don't use the method columns, so they are unaffected. `orders` is a table, so it rebuilds fully on each run.

## 2. Fix

**Immediate, in dbt (`models/staging/stg_payments.sql`).** Use the method that is present, once you've confirmed the values match:

```sql
renamed as (

    select
        id as payment_id,
        order_id,
        coalesce(payment_method, method) as payment_method,

        -- `amount` is currently stored in cents, so we convert it to dollars
        amount / 100 as amount

    from source

)
```

If `method` holds different labels, map them explicitly, for example with a `case` statement, instead of using `coalesce`. Also fail loudly on unmapped values rather than letting them become NULL.

**Upstream.** The ingestion job should be fixed so `payment_method` is populated consistently and `method` is retired or documented. The dbt change is a mitigation until then.

**Rebuild.** `dbt build --select stg_payments+` rebuilds `orders` and `customers`. `daily_revenue` is an incremental model that doesn't use the method columns, so it doesn't need a backfill.

## 3. Test that would have caught this

Add these to the existing schema files:

```yaml
# models/staging/schema.yml
  - name: stg_payments
    columns:
      - name: payment_method
        tests:
          - not_null
          - accepted_values:
              arguments:
                values: ['credit_card', 'coupon', 'bank_transfer', 'gift_card']
```

`not_null` makes the NULL rows fail. Note that `accepted_values` alone allowed them through.

A reconciliation test catches the symptom at the orders level, regardless of which column is wrong. Create `tests/assert_payment_methods_sum_to_amount.sql`:

```sql
-- Returns rows (and therefore fails) when the per-method amounts don't add up to the order amount.
select
    order_id,
    amount,
    coalesce(credit_card_amount, 0) + coalesce(coupon_amount, 0)
        + coalesce(bank_transfer_amount, 0) + coalesce(gift_card_amount, 0) as methods_total
from {{ ref('orders') }}
where abs(amount - (
    coalesce(credit_card_amount, 0) + coalesce(coupon_amount, 0)
        + coalesce(bank_transfer_amount, 0) + coalesce(gift_card_amount, 0)
)) > 0.005
```

The existing `not_null` tests on `orders.*_amount` should be left alone, but they don't test anything useful here, since the pivot never returns NULL.
