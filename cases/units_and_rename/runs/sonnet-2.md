claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 39 s | USD 0.086 | 2026-10-14 | project bef6b69 | prompt aaad08d98757

## 1. Root cause

**`models/staging/stg_payments.sql`** reads only `payment_method`, but the March and April rows in `raw_payments` no longer fill that column. The raw table has both `payment_method` and `method`, so the ingestion job appears to have switched to `method` from March onward. January and February rows still use `payment_method`. I can't see the seed data, so I'm inferring this from the schema and the NULLs.

- **Zero by method:** `stg_payments.payment_method` is NULL for those rows (payments 66, 67, 86, 105, 106). In `orders.sql`, every `case when payment_method = '...'` falls through to 0. The total (`sum(amount)`) still works, which is why March shows 595.27 against 0.00 for every method.
- **Tests still pass:** `accepted_values` ignores NULLs, and the `not_null` tests on the `*_amount` columns pass because the `else 0` branch never produces NULL. Nothing checks that `payment_method` is populated or that the method columns add up to `amount`.

**The totals are also wrong, by a separate unit problem.** The gaps don't match the missing rows, so they come from somewhere else:

- April is 139.00 settled against 106.33 in our table, a gap of 32.67. That equals the 33.00 of coupons minus 0.33.
- The 0.33 is the sum of the NULL-method amounts in the sample: 0.02 (order 92) + 0.07 (94) + 0.24 (95). That means raw amounts of 2, 7 and 24, which are whole dollars, not cents.
- `stg_payments` divides every row by 100, so these coupon rows are counted as 0.02, 0.07 and 0.24 instead of 2, 7 and 24.
- March fits the same pattern: 0.02 + 0.02 + 0.23 = 0.27 from the sampled orders 76, 81 and 86, plus an 18.00 coupon, gives 18.27. The correct figure is 45.00, and 595.27 − 18.27 + 45.00 = 622.00, which matches settlement.
- So the coupon rows in the new feed appear to be in dollars, while the other methods are in cents. This is the part I'm least sure of. Check it against `raw_payments` before relying on it.

## 2. Fix

Confirm the diagnosis first:

```sql
select
  strftime(o.ordered_at, '%Y-%m') as month,
  p.payment_method is null as pm_null,
  p.method,
  count(*), sum(p.amount) as raw_amount
from raw_payments p join raw_orders o on o.id = p.order_id
group by all order by 1, 2, 3
```

Then update `stg_payments.sql`. The unit rule below is my reading of the data, so confirm it with the ingestion owner:

```sql
renamed as (

    select
        id as payment_id,
        order_id,
        coalesce(payment_method, method) as payment_method,

        -- `amount` is stored in cents, except coupon rows loaded via `method`, which are whole dollars
        case
            when payment_method is null and method = 'coupon' then amount
            else amount / 100
        end as amount

    from source

)
```

The better long-term fix is to have the ingestion job write a single column with a single unit, and to backfill March and April. If the `method` values aren't already lowercase snake_case, normalize them in the same step.

After the change, March should show coupons of 45.00 and a total of 622.00. April should show coupons of 33.00 and a total of 139.00.

## 3. Tests that would have caught it

**In `staging/schema.yml`**, add `not_null` to `payment_method`. `accepted_values` alone lets NULLs through:

```yaml
      - name: payment_method
        tests:
          - not_null
          - accepted_values: ...
```

**Singular test**, `tests/assert_order_method_amounts_sum_to_amount.sql`. It fails for every March and April order today:

```sql
select order_id, amount,
       credit_card_amount + coupon_amount + bank_transfer_amount + gift_card_amount as by_method
from {{ ref('orders') }}
where abs(amount - (credit_card_amount + coupon_amount + bank_transfer_amount + gift_card_amount)) > 0.005
```

**Source-level guard**, so a column switch like this one is flagged at load time. It fails when any raw payment has neither method column filled:

```sql
select id from {{ ref('raw_payments') }}
where payment_method is null and method is null
```

The tests above catch the missing column. The unit problem also needs a monthly reconciliation of `sum(amount)` against the processor settlement total. A cheap unit check is that no payment's raw amount is suspiciously small (for example under 100) unless it is a coupon.
