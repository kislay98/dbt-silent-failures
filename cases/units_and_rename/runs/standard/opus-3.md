claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 44 s | USD 0.212 | 2026-10-15 | project ad0b16b | prompt 59943c0b3f30

# Diagnosis: the March/April payment mix in `orders`

## 1. Root cause

The upstream `raw_payments` feed changed shape partway through March. The project's code is unchanged and still reads the old layout.

**a) The payment method moved to a new column.** `raw_payments` now has an extra `method` column. For the new rows, the original `payment_method` column is `NULL`:

```
id 112 | order 98 | payment_method NULL          | amount 1000 | method bank_transfer
id  28 | order 25 | payment_method bank_transfer | amount 2000 | method bank_transfer
```

`models/staging/stg_payments.sql` still selects `payment_method`, so every payment from the new feed has a `NULL` method. This affects `models/orders.sql` in two ways:
- Every `case when payments.payment_method = '...'` falls through to `else 0`, so the per-method amounts are 0.00.
- The `left join payment_methods` finds no match, so `coalesce(fee_rate, 0)` gives `fees = 0`.

The `amount` total is still populated because `sum(payments.amount)` ignores the method. That is why `orders` shows 0.00 by method but a non-zero total.

**b) Coupon amounts in the new feed appear to be in dollars, not cents.** `stg_payments` divides every amount by 100. That shrinks dollar-valued rows 100×, for example payment 86 shows 0.02 instead of 2.00. The arithmetic matches the settlement exactly:

| | `orders` total | Settlement | Gap | Explained by |
|---|---|---|---|---|
| March | 595.27 | 622.00 | 26.73 | 27.00 of new-feed coupons stored as 27 and read as 0.27 |
| April | 106.33 | 139.00 | 32.67 | 33.00 of coupons (all of April's, per marketing) read as 0.33 |

The .27 and .33 remainders only work out if exactly 27.00 and 33.00 of payments were divided by 100 twice. April's 33.00 matches marketing's coupon ledger exactly. March's 27.00 is part of its 45.00, the rest being pre-switch rows in cents. I couldn't query the warehouse, so please confirm with:

```sql
select date_trunc('month', o.order_date) as month, p.method, count(*), sum(p.amount) as raw_amount
from raw_payments p join stg_orders o on o.order_id = p.order_id
where p.payment_method is null
group by 1, 2 order by 1, 2;
```

I expect the coupon rows to come back as 27 (March) and 33 (April), while the other methods are in cents.

**Why the tests passed:**
- `accepted_values` treats `NULL` as passing.
- The `not_null` tests on `orders.*_amount` can't catch this, because the `else 0` turns missing methods into zeros.
- Nothing checks that the method amounts add up to the total, or that the totals reconcile.

**Side note on `daily_revenue`:** it shows 622.00 and 139.00 because it is incremental and only rebuilds the last 3 days. Those rows were written before the bad data arrived. It is stale, not a trustworthy reference, and would show the wrong totals after a `--full-refresh`.

## 2. Fix

The best fix is upstream: get the ingestion team to keep populating `payment_method` and to send all amounts in cents. Until then, normalise the data in staging, in `models/staging/stg_payments.sql`:

```sql
renamed as (

    select
        id as payment_id,
        order_id,
        -- the payments feed moved the method to `method` in March 2018; older rows only have `payment_method`
        coalesce(payment_method, method) as payment_method,

        -- `amount` is stored in cents, except coupons from the new feed, which arrive in dollars
        case
            when payment_method is null and method = 'coupon' then amount
            else amount / 100
        end as amount

    from source

)
```

Only make the coupon/units branch if the query above confirms it. If other methods in the new feed are also in dollars, widen the condition to cover them. Then run:

```
dbt build --full-refresh -s stg_payments+
```

The full refresh is needed so `daily_revenue` is recomputed from corrected data rather than keeping old rows. After the fix, March should total 622.00 with coupons at 45.00, April 139.00 with coupons at 33.00, and fees non-zero again.

## 3. Tests that would have caught it

In `models/staging/schema.yml`, `accepted_values` alone lets `NULL` through, so add `not_null`:

```yaml
      - name: payment_method
        tests:
          - not_null
          - accepted_values:
              arguments:
                values: ['credit_card', 'coupon', 'bank_transfer', 'gift_card']
```

Add a reconciliation test checking that the method breakdown adds up to the total, in `tests/assert_order_payment_methods_sum_to_amount.sql`:

```sql
select order_id
from {{ ref('orders') }}
where abs(credit_card_amount + coupon_amount + bank_transfer_amount + gift_card_amount - coalesce(amount, 0)) > 0.005
```

Either of these would have failed on the nightly build.

The units problem (b) is subtler, and neither test above catches it. Two options:
- **Best:** a singular test reconciling monthly `orders.amount` against the processor settlement, if that data can be loaded as a source.
- **Cheap guard:** a schema check on `raw_payments`, such as `dbt_expectations.expect_table_columns_to_match_set`. This fails as soon as the feed's columns change, which is when someone should check for unit changes too.
