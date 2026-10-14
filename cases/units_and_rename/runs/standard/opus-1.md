claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 48 s | USD 0.217 | 2026-10-15 | project ad0b16b | prompt 59943c0b3f30

## 1. Root cause

The upstream `raw_payments` feed changed shape partway through the data. `jaffle_shop/models/staging/stg_payments.sql` was not updated, and no test catches the change. Two things are wrong in `raw_payments` for March and April rows.

**a) Payment method moved to a new column.** The table now has a `method` column. From March onward, `payment_method` is `NULL` and the value is only in `method`. For example, payment 112 has `payment_method = NULL` and `method = bank_transfer`. `stg_payments` still selects `payment_method`, so in `orders.sql`:
- Every `case when payments.payment_method = '...'` is false, so all four `*_amount` columns are 0.00.
- The join to `raw_payment_methods` misses, and `coalesce(fee_rate, 0)` turns fees into 0.00.
- `total_amount` (`sum(payments.amount)`) ignores the method, which is why the month total is still populated.

The tests pass because the `accepted_values` test on `stg_payments.payment_method` skips NULLs (`NULL not in (...)` is not true). The `not_null` tests on `orders.*_amount` can't fail because the CASE expression always yields 0.

**b) Some new-feed amounts are in dollars, not cents.** `stg_payments` divides every amount by 100. Most raw amounts are whole-dollar cent values (1000, 1800, 2900), but some new rows are already in dollars. For example, payment 86 has raw value 2, which comes out as 0.02. That explains the fractional totals (595.27, 106.33, 312.02, 1101.02). The gaps match exactly:
- March: 622.00 − 595.27 = 26.73 = 27 × 0.99, so $27 of payments were divided by 100 by mistake.
- April: 139.00 − 106.33 = 32.67 = 33 × 0.99, so $33 was. That equals April's coupon total.

My inference is that these are coupon rows, but I'm working from aggregates. March has $45 of coupons but only $27 is affected, so verify the row set before fixing:

```sql
select date_trunc('month', o.ordered_at) as month, p.method, count(*), sum(p.amount)
from raw_payments p join raw_orders o on o.id = p.order_id
where p.amount % 100 <> 0
group by 1, 2 order by 1, 2;
```

**Why `daily_revenue` still shows the right numbers (622 / 139):** it is incremental and only rebuilds `order_date >= max(order_date) - 3 days`. March and the early-April days were built before the feed broke, so they hold stale but correct values. It is not evidence that the pipeline is healthy. A `--full-refresh` today would corrupt it too.

## 2. Fix

Preferably, fix the data at the source: have ingestion backfill March and April with `payment_method` populated and `amount` in cents. Whatever the source fix is, make staging tolerate both column layouts:

```sql
-- jaffle_shop/models/staging/stg_payments.sql
renamed as (

    select
        id as payment_id,
        order_id,
        -- the feed moved payment_method into `method` from March 2018
        coalesce(payment_method, method) as payment_method,

        -- `amount` is currently stored in cents, so we convert it to dollars
        amount / 100 as amount

    from source

)
```

The coalesce alone fixes the by-method breakdown and the fees. For the units, have ingestion reload the affected rows as cents (e.g. 2 → 200).

I'd avoid a heuristic like `case when amount % 100 <> 0 then amount else amount / 100 end` except as a stopgap. It can't tell a $1 payment in dollars (raw 100) from a $1 payment in cents (raw 100).

Then rebuild:

```
dbt build --full-refresh -s stg_payments+
```

Afterwards, check that `orders` by month shows March at 622.00 and April at 139.00, coupons at 45.00 and 33.00, and the method columns summing to `amount`.

## 3. Tests that would have caught this

In `models/staging/schema.yml`, `not_null` would have failed immediately:

```yaml
  - name: stg_payments
    columns:
      - name: payment_method
        tests:
          - not_null
          - accepted_values:
              arguments:
                values: ['credit_card', 'coupon', 'bank_transfer', 'gift_card']
```

A reconciliation test in `tests/assert_order_payment_methods_sum_to_amount.sql` catches any payment that falls outside the four method buckets:

```sql
select order_id, amount,
       credit_card_amount + coupon_amount + bank_transfer_amount + gift_card_amount as by_method
from {{ ref('orders') }}
where abs(amount - (credit_card_amount + coupon_amount + bank_transfer_amount + gift_card_amount)) > 0.005
```

A units guard in `tests/assert_raw_payments_in_whole_cents_of_dollars.sql` catches the dollars-vs-cents drift. It assumes all payments are whole dollars, which holds for this dataset, so use `severity: warn` if that could change:

```sql
select id, amount from {{ ref('raw_payments') }} where amount % 100 <> 0
```

To guard against the next schema change, consider `dbt-expectations`' `expect_table_columns_to_match_set` on `raw_payments`. It would have flagged the new `method` column on the first night it appeared.
