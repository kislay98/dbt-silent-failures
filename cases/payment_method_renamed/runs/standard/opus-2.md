claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 28 s | USD 0.179 | 2026-10-14 | project ad0b16b | prompt 57278f2875bf

# Payment mix diagnosis: March and April payment methods are NULL

## 1. Root cause

The upstream payments feed changed shape, and `models/staging/stg_payments.sql` still reads the old column.

`raw_payments` now has two method columns: the original `payment_method` and a new `method`. In the sample, rows for later orders (57, 65, 71, 76, 79, 81, 85, 97, 98) have `payment_method = NULL`. For those rows the value is only in `method`. Earlier rows (orders 11, 25, 51) have both columns populated and they agree. So the ingestion job started writing the method into `method` from roughly the March 2018 orders onward. Order 57 (2018-03-01) is already NULL, and order 51 (2018-02-24) is not.

`stg_payments.sql` selects only `payment_method`, so every March and April payment reaches `orders.sql` with a NULL method. That produces the following in `models/orders.sql`:

- **Method columns are zero.** `sum(case when payments.payment_method = 'credit_card' then amount else 0 end)` matches nothing, so `credit_card_amount`, `coupon_amount`, `bank_transfer_amount` and `gift_card_amount` all come out as 0.0, not NULL.
- **Fees are zero.** The join to `raw_payment_methods` misses, and `coalesce(fee_rate, 0)` turns `fees` into 0.0.
- **Totals are still right.** `sum(payments.amount)` doesn't depend on the method, which is why monthly revenue, `daily_revenue`, `customers` and `region_revenue` all still reconcile.

Every test passed because none of them look at NULL methods:

- `accepted_values` on `stg_payments.payment_method` ignores NULLs by design.
- The `not_null` tests on `orders.*_amount` and `fees` can't fail, because the `else 0` and `coalesce(..., 0)` turn missing data into zeros.

## 2. Fix

In `models/staging/stg_payments.sql`, read the method from whichever column the feed populated:

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

Before shipping, confirm that the two columns never disagree and that nothing is left uncovered. Both of these should return 0:

```sql
select count(*) from raw_payments
where payment_method is not null and method is not null and payment_method <> method;

select count(*) from raw_payments
where payment_method is null and method is null;
```

Then rebuild:

```sh
dbt build -s stg_payments+ --full-refresh
```

`--full-refresh` matters for `daily_revenue`. Its values didn't change here, but it's the safe default after a staging change. After the rebuild, March `credit_card` in `orders` should come to the processor's 334.00, and March and April `fees` should be non-zero.

Also ask the ingestion owners whether `payment_method` is deprecated. If it is, switch to `method` alone and drop the coalesce once the backfill is confirmed.

## 3. Tests that would have caught this

**a) `not_null` on the staging method.** `accepted_values` doesn't check for NULLs. Add this in `models/staging/schema.yml`:

```yaml
      - name: payment_method
        tests:
          - not_null
          - accepted_values:
              arguments:
                values: ['credit_card', 'coupon', 'bank_transfer', 'gift_card']
```

This alone would have failed the nightly build.

**b) A reconciliation test on `orders`.** The payment mix must sum to the total. This catches any unmapped or new method, whatever the cause. Create `tests/assert_orders_payment_mix_sums_to_amount.sql`:

```sql
{% set payment_methods = ['credit_card', 'coupon', 'bank_transfer', 'gift_card'] %}

select
    order_id,
    amount,
    {% for payment_method in payment_methods -%}
    {{ payment_method }}_amount{{ ' + ' if not loop.last }}
    {%- endfor %} as payment_mix_amount

from {{ ref('orders') }}

where abs(
    coalesce(amount, 0)
    {% for payment_method in payment_methods -%}
    - {{ payment_method }}_amount
    {% endfor -%}
) > 0.005
```

Of the two, (b) is the stronger guard. It would also fail if someone adds a fifth payment method upstream without updating the `payment_methods` list in `orders.sql`.
