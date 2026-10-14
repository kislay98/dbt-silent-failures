claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 45 s | USD 0.119 | 2026-10-15 | project ad0b16b | prompt 59943c0b3f30

## 1. Root cause

**`raw_payments` has drifted: it now has two payment-method columns, `payment_method` and `method`, and from March onward the ingestion job fills only `method`.** `models/staging/stg_payments.sql` reads only `payment_method`, so every March and April payment gets `payment_method = NULL`.

The evidence:
- The raw sample shows rows like `id=112, payment_method=NULL, method='bank_transfer'` and `id=90, payment_method=NULL, method='credit_card'`.
- `stg_payments` shows the same rows with `payment_method NULL`.
- In `models/orders.sql`, every `case when payments.payment_method = '...'` is false for those rows, so the four `*_amount` columns are 0.00. `amount` still comes from `sum(payments.amount)`, so the total is non-zero.
- The left join to `raw_payment_methods` never matches, and `coalesce(fee_rate, 0)` hides that, so `fees` is also 0.00.

It passed because `accepted_values` on `stg_payments.payment_method` ignores NULLs, and there is no `not_null` test on that column. The `not_null` tests on `orders.*_amount` pass because the `case ... else 0` produces zeros, not NULLs.

There is a second, separate problem in the amounts, and I'm inferring it from the arithmetic:
- Settlement minus `orders` is 622.00 − 595.27 = 26.73 = 27 − 0.27 for March.
- For April it is 139.00 − 106.33 = 32.67 = 33 − 0.33.
- So payments totalling 27 (March) and 33 (April) are being counted at 1/100 of their value.
- The `0.02` and similar amounts in `stg_payments` support this: the raw value looks like whole dollars, but `amount / 100` in `stg_payments.sql` treats it as cents.
- April's 33 matches marketing's April coupon total exactly, so these are likely coupon rows. March's 27 is less than the 45 in coupons, so only part of March's coupons are affected.

I can't see the raw rows, so confirm this with a query. Look at `payment_method is null` rows grouped by `method`, and at rows where `amount % 100 <> 0`.

`daily_revenue` is stale, not correct. It matches the settlement figures because it was built before the data changed. The incremental filter only rebuilds the last 3 days, so March and early April were never recomputed. It will quietly diverge from `orders` until a full refresh.

## 2. Fix

In `models/staging/stg_payments.sql`:

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

- **Units:** get the ingestion owner to confirm and fix the unit for the affected rows, ideally with a reload of March and April. If the dollar-valued rows turn out to be identifiable (for example `method = 'coupon'` from a given date), add an explicit `case` for them in staging. Don't guess from `amount % 100`, because it will misfire on legitimate small amounts.
- **Rebuild:** run `dbt build --full-refresh -s stg_payments+ daily_revenue`. A plain `dbt build` will not fix `daily_revenue`.
- **Afterwards:** March should show 622.00 with coupon 45.00 and non-zero fees. April should show 139.00 with coupon 33.00.

## 3. Tests that would have caught it

Add a `not_null` test to `stg_payments.payment_method` in `models/staging/schema.yml`:

```yaml
      - name: payment_method
        tests:
          - not_null
          - accepted_values:
              arguments:
                values: ['credit_card', 'coupon', 'bank_transfer', 'gift_card']
```

Add a singular test, `tests/assert_orders_methods_sum_to_amount.sql`, which fails on every March and April order:

```sql
select order_id, amount,
       credit_card_amount + coupon_amount + bank_transfer_amount + gift_card_amount as by_method
from {{ ref('orders') }}
where abs(amount - (credit_card_amount + coupon_amount + bank_transfer_amount + gift_card_amount)) > 0.005
```

Add a second singular test, `tests/assert_daily_revenue_matches_orders.sql`, to catch the stale incremental:

```sql
select o.order_date, o.revenue, d.revenue as stored
from (select order_date, sum(amount) as revenue from {{ ref('orders') }} group by 1) o
full join {{ ref('daily_revenue') }} d using (order_date)
where abs(coalesce(o.revenue, 0) - coalesce(d.revenue, 0)) > 0.005
```

Optionally, add a source freshness and schema check on `raw_payments` that fails if `payment_method` is NULL while `method` is not.
