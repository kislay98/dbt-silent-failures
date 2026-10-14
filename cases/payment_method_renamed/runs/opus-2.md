claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 25 s | USD 0.124 | 2026-10-14 | project f769fdf | prompt 0206530f44fe

## 1. Root cause

**The data changed. The model code is still correct for the old data.** The load writes March onward with an empty payment method column.

The warehouse schema shows that `raw_payments` now has two method columns:

```
raw_payments │ payment_method │ VARCHAR
raw_payments │ amount         │ INTEGER
raw_payments │ method         │ VARCHAR   ← new, appended at the end
```

From March 2018 the ingestion job writes the payment method into the new `method` column and leaves `payment_method` NULL. Everything after that follows from the models:

- **`models/staging/stg_payments.sql`** selects only `payment_method`, so every March+ payment gets `payment_method = NULL`.
- **`models/orders.sql`** builds each bucket with `case when payment_method = '...' then amount else 0 end`. For NULL, every `case` falls through to `0`, so all four buckets are `0.0`. `sum(amount) as total_amount` doesn't look at the method, so `amount` is still correct. That's why the month total (622) and the settlement match while the mix is all zeros. April (139) has the same problem; finance just hasn't checked it yet.

**Why `dbt build` passed:** `accepted_values` on `stg_payments.payment_method` ignores NULLs, because `NULL not in (...)` is not true. No test checks that `payment_method` is not null. No test checks that the buckets add up to `amount`.

I worked this out from the schema and the output you pasted. I haven't queried the data. This query confirms it:

```sql
select date_trunc('month', o.ordered_at) as month,
       p.payment_method, p.method,
       count(*) as n, sum(p.amount) / 100 as amount
from raw_payments p
join raw_orders o on p.order_id = o.id
group by all
order by 1, 2, 3;
```

You should see Jan and Feb filled in `payment_method` with `method` NULL, and March and April the other way round. March's card rows should total about 334.00, which matches the processor statement. Also check which values `method` uses. If they aren't the same four labels, add a mapping in the fix below.

## 2. Fix

Coalesce the two columns in staging. Edit `models/staging/stg_payments.sql`:

```sql
renamed as (

    select
        id as payment_id,
        order_id,
        -- the ingestion job moved the method to `method` from 2018-03; older rows still use `payment_method`
        coalesce(payment_method, method) as payment_method,

        -- `amount` is currently stored in cents, so we convert it to dollars
        amount / 100 as amount

    from source

)
```

If `method` uses a different vocabulary (e.g. `card` instead of `credit_card`), wrap it in a `case` that maps it to the four existing values. The tests below will fail if anything is left unmapped. Then run `dbt build`. `daily_revenue` is unaffected, because it only uses `amount`.

Also raise this with the ingestion owners. The rename should be applied consistently or announced as a contract change. Otherwise the next column change will break the models quietly again.

## 3. Tests that would have caught it

**a) A `not_null` test on the method.** This one test would have failed the March build. In `models/staging/schema.yml`:

```yaml
      - name: payment_method
        tests:
          - not_null
          - accepted_values:
              arguments:
                values: ['credit_card', 'coupon', 'bank_transfer', 'gift_card']
```

**b) A reconciliation test on `orders`.** This catches any payment that isn't counted in a bucket, whether its method is NULL, new or misspelled. Create `tests/assert_orders_payment_methods_sum_to_amount.sql`:

```sql
-- every order's per-method amounts must add up to its total amount
select
    order_id,
    amount,
    credit_card_amount + coupon_amount + bank_transfer_amount + gift_card_amount as method_total

from {{ ref('orders') }}

where abs(
    coalesce(credit_card_amount, 0) + coalesce(coupon_amount, 0)
    + coalesce(bank_transfer_amount, 0) + coalesce(gift_card_amount, 0)
    - coalesce(amount, 0)
) > 0.001
```

Test (a) catches the specific cause. Test (b) protects the report itself, so keep both.

One unrelated thing I noticed: `models/schema.yml` documents a column `total_order_amount` on `customers`. That column doesn't exist; the model outputs `customer_lifetime_value`.
