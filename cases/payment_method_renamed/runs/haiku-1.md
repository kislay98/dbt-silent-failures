claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 31 s | USD 0.005 | 2026-10-14 | project bef6b69

## 1. Root cause

The March payment rows in `raw_payments` have a NULL `payment_method`. The value is in the other column, `method`, which the staging model never reads. The bug is in the data landing, but it reaches the report through `models/staging/stg_payments.sql`, and no test catches it.

Evidence from the paste:

- **Amounts are right, methods are zero.** `orders.amount` for March is 622.00, which comes from `sum(amount)` over all payments. The four method columns are 0 because `orders.sql` has `case when payment_method = '...' then amount else 0 end`. A NULL `payment_method` matches none of the branches, so every March payment falls into `else 0`.
- **The values aren't an unexpected string.** If March had values like `'Credit Card'` or `'cc'`, the `accepted_values` test on `stg_payments.payment_method` would fail. It passes because dbt's `accepted_values` checks `value not in (...)`, which evaluates to NULL for NULL inputs, so NULL rows are never selected. The passing test is misleading here.
- **The schema has a second payment column.** `raw_payments` has both `payment_method` and `method`. `stg_payments.sql` selects only `payment_method`. This looks like ingestion schema drift: the March load wrote the method into `method`.
- **The zero is not a missing join.** A payment-less order would show NULL in the method columns, not 0. The zeros mean payments exist with an unrecognized method.

Affected files:
- `jaffle_shop/models/staging/stg_payments.sql`: reads only `payment_method`, so March's method is lost.
- `jaffle_shop/models/orders.sql`: `else 0` hides the unmatched payments, so nothing fails.
- `jaffle_shop/models/staging/schema.yml`: `payment_method` has `accepted_values` but no `not_null`.
- Raw table `raw_payments`: the March rows have NULL `payment_method` with the value in `method`.

I can't query the warehouse from this paste, so confirm the cause before changing anything:

```sql
select
    date_trunc('month', o.ordered_at) as month,
    p.payment_method,
    p.method,
    count(*) as n,
    sum(p.amount) / 100 as amount
from raw_payments p
join raw_orders o on o.id = p.order_id
group by all
order by all;
```

If March's `payment_method` is NULL and `method` holds the four values, the diagnosis is confirmed. If `method` holds different codes (for example `CC`), the fix needs an explicit mapping, not a plain coalesce.

## 2. Fix

Staging is the right place, because it is where the raw-to-model mapping lives. Once `method` is confirmed to use the same four values:

```sql
-- jaffle_shop/models/staging/stg_payments.sql
renamed as (

    select
        id as payment_id,
        order_id,
        -- March 2018 rows landed with payment_method NULL and the value in `method`
        coalesce(payment_method, method) as payment_method,

        -- `amount` is currently stored in cents, so we convert it to dollars
        amount / 100 as amount

    from source

)
```

Also add the missing test in `jaffle_shop/models/staging/schema.yml`:

```yaml
  - name: stg_payments
    columns:
      - name: payment_method
        tests:
          - not_null
          - accepted_values: ...   # existing
```

Also fix the ingestion job so it writes `payment_method` for every load. A one-off `update raw_payments set payment_method = method where payment_method is null` would be overwritten on the next load, so don't rely on it alone.

Then run:

```
dbt build --select stg_payments+
```

Only the method columns change. Amounts, totals, and `daily_revenue` stay the same, so no full refresh is needed.

## 3. Test that would have caught it

Add a singular test that checks each order's method amounts add up to its total. It fails on March today.

```sql
-- jaffle_shop/tests/assert_payment_methods_sum_to_amount.sql
select
    order_id,
    amount,
    credit_card_amount + coupon_amount + bank_transfer_amount + gift_card_amount as methods_total
from {{ ref('orders') }}
where abs(coalesce(amount, 0) - (credit_card_amount + coupon_amount + bank_transfer_amount + gift_card_amount)) > 0.001
```

The existing tests missed this for two reasons. The per-method `not_null` tests pass on 0, and `accepted_values` ignores NULLs. The new `not_null` on `stg_payments.payment_method` catches the NULL at its source, and the sum check catches any future unmatched method, whatever its cause.

## Other things I noticed (not the cause)

- `customers` is documented with `total_order_amount` in `models/schema.yml`, but the model outputs `customer_lifetime_value`. Dbt won't fail on this, but the docs are wrong.
- `region_revenue` joins `raw_customer_regions`, which has an `updated_at` column. If a customer has more than one row there, `region_revenue` double-counts revenue. The seed has no uniqueness test on `customer_id`, so this would pass silently. Worth adding `unique` on `customer_id` in the seed's schema.
