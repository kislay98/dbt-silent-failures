claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 17 s | USD 0.057 | 2026-10-14 | project bef6b69

## 1. Root cause

The ingestion job changed the shape of `raw_payments`. The table now has two method columns, `payment_method` and a new trailing `method`, which is not in the original jaffle_shop schema. The `raw_payments` column list in the warehouse shows it. I can't see the seed data, but the symptoms fit March-onward payment rows being loaded with `method` populated and `payment_method` NULL.

- **`models/staging/stg_payments.sql`** only reads `payment_method`, so for those rows it is NULL.
- **`models/orders.sql`** then does `sum(case when payment_method = 'credit_card' ... else 0 end)`. A NULL method matches none of the four branches, so every per-method column is `0.0`.
- `amount` is `sum(amount)` with no method filter, so it is still correct (622.00 for March). That is why total revenue matches settlement and `daily_revenue` looks fine.
- Orders 57-63 in the sample show this: `amount` is populated and all four method columns are 0.0. Orders up to 56 are fine. April is affected too (139.00 total, 0 in every method).

The tests passed for these reasons:

- `accepted_values` on `stg_payments.payment_method` ignores NULLs, so it passes.
- There is no `not_null` test on `payment_method`.
- The `not_null` tests on `orders.*_amount` pass because the `else 0` makes the sums 0, not NULL.
- Nothing checks that the per-method columns add up to `amount`.

Please confirm with this query:

```sql
select date_trunc('month', o.order_date) m, p.payment_method is null as pm_null, p.method, count(*), sum(p.amount)/100
from raw_payments p join raw_orders o on o.id = p.order_id
group by all order by 1;
```

Also check that the distinct `method` values use the same vocabulary (`credit_card`, `coupon`, and so on). If they don't, map them in the fix below.

## 2. Fix

In `models/staging/stg_payments.sql`:

```sql
    select
        id as payment_id,
        order_id,
        coalesce(payment_method, method) as payment_method,

        -- `amount` is currently stored in cents, so we convert it to dollars
        amount / 100 as amount

    from source
```

If the new `method` values use different labels, add a `case` mapping them to the four canonical values. After the change, March card volume should come to the processor's 334.00.

Also ask the ingestion owner to either keep writing to `payment_method` or announce the rename and backfill. Once the old column is retired, drop the `coalesce`.

## 3. Tests that would have caught this

In `models/staging/schema.yml`, add `not_null` to `payment_method`. This fails today, and it is the direct catch:

```yaml
      - name: payment_method
        tests:
          - not_null
          - accepted_values:
              arguments:
                values: ['credit_card', 'coupon', 'bank_transfer', 'gift_card']
```

Add a singular test, `tests/assert_order_method_amounts_sum_to_amount.sql`, so the breakdown must reconcile with the total:

```sql
select order_id, amount,
       credit_card_amount + coupon_amount + bank_transfer_amount + gift_card_amount as method_sum
from {{ ref('orders') }}
where abs(coalesce(amount, 0) - (credit_card_amount + coupon_amount + bank_transfer_amount + gift_card_amount)) > 0.005
```

That test fails for every order from 57 onward. To catch schema drift earlier, you could also add a source or seed contract that pins the expected columns of `raw_payments`, for example `contract: {enforced: true}` or an `equal_rowcount`/column-set check, so an unexpected `method` column fails the build.
