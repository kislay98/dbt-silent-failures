claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 25 s | USD 0.091 | 2026-10-14 | project ad0b16b | prompt a85110595fb3

## 1. Root cause

**`raw_payments` (loaded by the ingestion job) has two columns for the payment method: `payment_method` and `method`.** For the newer payments, which are the March and April orders, the loader writes the value into `method` and leaves `payment_method` NULL. The sample shows it: payment 93 has `payment_method = NULL, method = 'coupon'`, while payment 28 has both populated.

**`models/staging/stg_payments.sql`** only reads `payment_method`, so those rows reach `stg_payments` with a NULL method. Downstream:

- **`orders.sql`**: every `case when payment_method = '...'` is false, so the per-method amounts are 0.00 while `amount` (the plain sum) is still right. That is why the totals are 708 and 146.
- **`orders.sql`**: the left join to `raw_payment_methods` finds no match, so `fees` is 0 for those orders. This probably affects the fee figures finance is also looking at.

Every test passed because none of them can see it:
- `accepted_values` on `stg_payments.payment_method` ignores NULLs, which is dbt's standard behaviour.
- The `not_null` tests on `orders.*_amount` pass because `sum(case ... else 0)` is never NULL.
- Nothing checks that the method columns add up to `amount`.

**Secondary issue, inferred from the output rather than proven by the data:** `daily_revenue` shows 622 and 139 for March and April, against 708 and 146 in `orders`. The model only rebuilds the last 3 days relative to `max(order_date)`. Payments landing later than that never get picked up for older order dates, so those rows stay stale.

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

Ask the ingestion owners to stop writing to `method`, or to backfill `payment_method`. The `coalesce` is a safe bridge until then.

Then rebuild everything, because `daily_revenue` is incremental and won't heal itself:

```
dbt build --full-refresh
```

Expected result: March and April method columns sum to 708 and 146, March card volume should match the processor's 334.00, and `fees` become non-zero for those months.

To stop `daily_revenue` drifting again, either widen the lookback or key the window on a load timestamp rather than `order_date`. Also decide whether late payments beyond that window are possible at all.

## 3. Tests that would have caught it

`models/staging/schema.yml`, since `accepted_values` doesn't catch NULLs:

```yaml
      - name: payment_method
        tests:
          - not_null
          - accepted_values:
              arguments:
                values: ['credit_card', 'coupon', 'bank_transfer', 'gift_card']
```

`tests/assert_orders_methods_sum_to_amount.sql`, a singular test that fails on any order whose method split doesn't add up:

```sql
select order_id
from {{ ref('orders') }}
where abs(
    credit_card_amount + coupon_amount + bank_transfer_amount + gift_card_amount
    - coalesce(amount, 0)
) > 0.001
```

`tests/assert_daily_revenue_matches_orders.sql`, which would have caught the stale incremental:

```sql
select o.order_date, o.revenue as expected, d.revenue as actual
from (
    select order_date, sum(amount) as revenue
    from {{ ref('orders') }}
    group by 1
) o
left join {{ ref('daily_revenue') }} d using (order_date)
where abs(o.revenue - coalesce(d.revenue, 0)) > 0.001
```

I haven't run any of this, since I only have the logs and samples you pasted.
