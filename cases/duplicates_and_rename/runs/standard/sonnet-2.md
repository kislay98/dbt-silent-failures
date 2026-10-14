claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 22 s | USD 0.090 | 2026-10-14 | project ad0b16b | prompt a85110595fb3

## 1. Root cause

**`models/staging/stg_payments.sql`** reads only `raw_payments.payment_method`, but the ingestion job has started filling a second column, `method`, instead.

- In the raw table, `payment_method` and `method` both exist. Older rows (e.g. id 12 and id 28) have both populated and identical.
- Newer rows (id 86 and above, i.e. orders from about 1 March on) have `payment_method = NULL` and the real value only in `method`. Examples are id 93 (coupon), id 112 (bank_transfer) and id 90 (credit_card).
- `stg_payments` therefore passes NULL through, and the NULL rows match no `case when payments.payment_method = '...'` branch in `orders.sql`, so all four method columns are 0.
- The same rows have no match in `raw_payment_methods`, so `fees` is 0 via `coalesce(fee_rate, 0)`.
- `amount` is unaffected because `sum(payments.amount)` doesn't depend on the method. That is why March and April totals (708 and 146) are right while the mix is 0.

**Why every test passed:**
- `accepted_values` on `stg_payments.payment_method` ignores NULLs, and there is no `not_null` on that column.
- `not_null` on `orders.*_amount` and `fees` passes because the `case` sums and `coalesce` turn the missing values into 0 rather than NULL.
- Nothing checks that the method amounts add up to `amount`.

## 2. Fix

In `stg_payments.sql`:

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

Then run `dbt build`. March card volume should come back to the processor's 334.00, and fees should populate.

Separately, ask the ingestion owners to stop writing `method` alone, or to confirm that it is the new canonical column. If `method` is the new column, drop `payment_method` from the coalesce later.

**Second issue:** `daily_revenue` is out of sync with `orders` (March 622 vs 708, April 139 vs 146). The incremental model only rebuilds the last 3 days, so payments that land later than that never update earlier dates. Run `dbt run --full-refresh -s daily_revenue` now. Longer term, widen the lookback window or reconcile against `orders` (test below). I haven't confirmed why the rows went stale, since the log only shows the model being created.

## 3. Tests

**Column test** in `models/staging/schema.yml`, which would have caught this directly:

```yaml
      - name: payment_method
        tests:
          - not_null
          - accepted_values:
              arguments:
                values: ['credit_card', 'coupon', 'bank_transfer', 'gift_card']
```

**Reconciliation test** in `tests/assert_orders_method_amounts_sum_to_amount.sql`, which catches any future unmapped method or new column:

```sql
select
    order_id,
    amount,
    credit_card_amount + coupon_amount + bank_transfer_amount + gift_card_amount as method_total
from {{ ref('orders') }}
where amount is not null
  and abs(amount - (credit_card_amount + coupon_amount + bank_transfer_amount + gift_card_amount)) > 0.001
```

**Stale-model test** in `tests/assert_daily_revenue_matches_orders.sql`:

```sql
select o.order_date, o.revenue as expected, d.revenue as actual
from (select order_date, sum(amount) as revenue from {{ ref('orders') }} group by 1) o
left join {{ ref('daily_revenue') }} d using (order_date)
where abs(o.revenue - coalesce(d.revenue, 0)) > 0.001
```

I haven't run any of this against your warehouse. The analysis comes from the files, log and samples you pasted.
