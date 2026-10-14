claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 28 s | USD 0.135 | 2026-10-14 | project 8ee5734 | prompt 2e932ff37d63

## 1. Root cause

**The `raw_payments` feed changed shape partway through: from March 2018 the payment method arrives in a new column, `method`, and `payment_method` is NULL.** The warehouse columns show `raw_payments` now has both `payment_method` and `method`. `stg_payments` still reads only `payment_method`:

- `jaffle_shop/models/staging/stg_payments.sql` selects `payment_method` and ignores `method`. Every payment for order 57 onward (from 2018-03-01) has `payment_method = NULL`, as the `stg_payments` sample shows.
- `jaffle_shop/models/orders.sql`: none of the `case when payments.payment_method = '...'` branches match NULL, so each `*_amount` column becomes `0`. `sum(payments.amount)` doesn't depend on method, so `amount` stays right (708 / 146). That is the symptom finance saw.
- **Second effect, not yet noticed:** `fees` is also understated for March and April. The `left join payment_methods` finds no match on NULL, and `coalesce(fee_rate, 0)` turns that into zero fees.

**Why `dbt build` stayed green:**
- `accepted_values` on `stg_payments.payment_method` compiles to `... where payment_method not in (...)`. `NULL not in (...)` evaluates to NULL, not true, so NULL rows are never flagged.
- There is no `not_null` test on `stg_payments.payment_method`.
- The `not_null` tests on `orders.*_amount` can't fail, because `sum(case ... else 0 end)` always returns 0, never NULL.
- Nothing checks that the method columns add up to `amount`.

To confirm the cutover in the warehouse:

```sql
select date_trunc('month', o.ordered_at) as month,
       count(*)                as payments,
       count(p.payment_method) as has_payment_method,
       count(p.method)         as has_method,
       count(*) filter (where p.payment_method is not null and p.method is not null
                          and p.payment_method <> p.method) as conflicting
from raw_payments p join raw_orders o on o.id = p.order_id
group by 1 order by 1;
```

## 2. Fix

In `jaffle_shop/models/staging/stg_payments.sql`, read whichever column is populated:

```sql
renamed as (

    select
        id as payment_id,
        order_id,
        -- the payments feed renamed payment_method to method from March 2018; accept either
        coalesce(payment_method, method) as payment_method,

        -- `amount` is currently stored in cents, so we convert it to dollars
        amount / 100 as amount

    from source

)
```

Then run `dbt build -s stg_payments+`. `orders`, `customers` and `region_revenue` are tables or views and rebuild in full.

**Watch `daily_revenue`:** it is incremental and only rebuilds the last 3 days. It doesn't use the method columns, so it isn't affected this time. If a future fix changes `amount`, though, it needs `--full-refresh`.

Also tell the ingestion team about the rename. Once the feed is settled on one column name, the `coalesce` can be simplified.

**Another thing to check (unconfirmed):** after the fix, compare March `credit_card_amount` against the processor's 334.00. `stg_payments` has 118 payments, with IDs up to at least 114. Payment 114 for order 59 (28.00) has the same amount as payment 68 for the same order. It may be a real split payment like order 58's, or a duplicate re-sent by the feed. If March card volume comes out above 334, look at payments with IDs above 113 first.

## 3. Tests that would have caught it

In `jaffle_shop/models/staging/schema.yml`, add `not_null` to the method column. This fails immediately on the current data:

```yaml
      - name: payment_method
        tests:
          - not_null
          - accepted_values:
              arguments:
                values: ['credit_card', 'coupon', 'bank_transfer', 'gift_card']
```

Also add a reconciliation test, so that any unbucketed payment fails the build whatever the reason (NULL, new method, typo). Create `jaffle_shop/tests/assert_orders_payment_methods_sum_to_amount.sql`:

```sql
-- every dollar of an order's amount must be attributed to exactly one payment method
select
    order_id,
    amount,
    credit_card_amount + coupon_amount + bank_transfer_amount + gift_card_amount as method_total
from {{ ref('orders') }}
where abs(coalesce(amount, 0)
          - (credit_card_amount + coupon_amount + bank_transfer_amount + gift_card_amount)) > 0.005
```

On the current output this returns orders 57 onward. The `not_null` test catches this specific failure; the reconciliation test is the safety net for the whole category of failure.
