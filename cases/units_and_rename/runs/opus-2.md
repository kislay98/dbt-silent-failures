claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 46 s | USD 0.166 | 2026-10-14 | project f769fdf | prompt afa3f72d4d9f

## 1. Root cause

There are two separate data problems in `raw_payments`, both starting in March 2018. `models/staging/stg_payments.sql` passes both through without complaint.

**a) Payment method moved to a new column, so every method shows 0.00.**
`raw_payments` now has two method columns, `payment_method` and `method`. The ingestion job began writing the method into `method` from 2018‑03‑01 onward, which left `payment_method` NULL. `stg_payments` only reads `payment_method`, so every March and April payment has a NULL method (see payments 66, 67, 86, 105, 106). In `orders.sql`, `sum(case when payment_method = '...' then amount else 0 end)` matches nothing and returns 0.0 for every method, while `total_amount` still sums everything.

The tests didn't catch it:
- `accepted_values` ignores NULLs.
- The `not_null` tests on `orders.*_amount` run after the `else 0`, so they never see a NULL.

**b) Some coupon amounts were loaded in dollars instead of cents, so the totals are too low.**
`stg_payments` divides every `amount` by 100. The rows showing 0.02, 0.07, 0.23 and 0.24 (orders 76, 81, 86, 92, 94, 95) are coupons worth $2, $7 and so on that were loaded as dollars. The numbers reconcile exactly:

| Month | Settled − reported | ÷ 0.99 = mis‑scaled raw value | Coupon ledger |
|---|---|---|---|
| March | 622.00 − 595.27 = 26.73 | 27 (shows as 0.27) | 45 → 27 in dollars, 18 still in cents |
| April | 139.00 − 106.33 = 32.67 | 33 (shows as 0.33) | 33 → all in dollars |

So coupon rows switched to dollar units partway through March (on or before 2018‑03‑20, order 76). Earlier March coupons and all non‑coupon rows are still in cents. Order 92 shows this clearly: its 15.0 payment is correct and its 0.02 payment is a $2 coupon.

## 2. Fix

Run this query first to find the exact unit cutover. I haven't seen the raw rows, so the cutover is an inference from the totals above:

```sql
select p.id, p.order_id, cast(o.ordered_at as date) as order_date,
       p.payment_method, p.method, p.amount
from raw_payments p join raw_orders o on o.id = p.order_id
where coalesce(p.payment_method, p.method) = 'coupon'
  and o.ordered_at >= '2018-03-01'
order by p.id;
```

The fix belongs upstream: the ingestion job should write `payment_method` and should load coupons in cents. Then correct the existing rows, e.g. `update raw_payments set amount = amount * 100 where method = 'coupon' and id >= <first bad id>`. Changing only the warehouse data won't last if the nightly load overwrites it.

Until the loader is fixed, make staging tolerate both problems in `models/staging/stg_payments.sql`:

```sql
renamed as (

    select
        id as payment_id,
        order_id,

        -- the payments feed moved the method into `method` from 2018-03
        coalesce(payment_method, method) as payment_method,

        -- `amount` is stored in cents, except coupons loaded in dollars from payment <first bad id>
        case
            when coalesce(payment_method, method) = 'coupon' and id >= {{ var('coupon_dollars_from_payment_id') }}
                then amount
            else amount / 100
        end as amount

    from source

)
```

After that, run `dbt build --full-refresh -s daily_revenue+`. `daily_revenue` is incremental and only rebuilds the last 3 days, so its March and April rows will keep the wrong revenue unless it is fully refreshed. Then check the result: the March method columns should sum to 622.00 with coupon at 45.00, and April should sum to 139.00 with coupon at 33.00.

## 3. Tests that would have caught it

In `models/staging/schema.yml`, add `not_null` to `stg_payments.payment_method`. This catches (a) immediately.

```yaml
      - name: payment_method
        tests:
          - not_null
          - accepted_values:
              arguments:
                values: ['credit_card', 'coupon', 'bank_transfer', 'gift_card']
```

Add a reconciliation test, `tests/orders_payment_methods_sum_to_amount.sql`, which also catches (a) and any future method that isn't in the list:

```sql
select order_id
from {{ ref('orders') }}
where abs(credit_card_amount + coupon_amount + bank_transfer_amount + gift_card_amount - coalesce(amount, 0)) > 0.005
```

For (b), compare `orders` against an external control total, either the processor settlement or the coupon ledger, if one of them can be loaded as a source:

```sql
-- tests/coupon_amounts_match_ledger.sql
select l.month, l.coupon_total, o.coupon_total
from {{ source('marketing', 'coupon_ledger_monthly') }} l
left join (
    select date_trunc('month', order_date) as month, sum(coupon_amount) as coupon_total
    from {{ ref('orders') }} group by 1
) o on o.month = l.month
where abs(l.coupon_total - coalesce(o.coupon_total, 0)) > 0.005
```

A cheaper sanity check is a `warn`-severity test on `stg_payments` that flags amounts below $1 (`amount < 1`). Those amounts didn't exist before March and would have flagged 0.02, 0.07 and 0.23.

**Side note, unrelated to this bug:** `raw_customer_regions` has an `updated_at` column and only a `not_null` test on `customer_id`. If the feed keeps history, `region_revenue` will double‑count revenue. Consider adding a `unique` test on `customer_id`.
