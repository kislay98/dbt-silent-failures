claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 21 s | USD 0.088 | 2026-10-14 | project ad0b16b | prompt a85110595fb3

## 1. Root cause

**`raw_payments` has two columns for the payment method, and the ingestion job switched from one to the other partway through.** Rows loaded for the March and April orders have `payment_method = NULL` and the value in the new `method` column (e.g. id 93: `NULL` / `coupon`, id 90: `NULL` / `credit_card`). Older rows have both populated and equal (id 28, id 12).

**`models/staging/stg_payments.sql`** only reads `payment_method`, so every payment from the new feed gets a NULL method. Downstream:

- **`orders.sql`:** the `case when payments.payment_method = '...'` pivot matches nothing, so all four method columns are 0.00 for those orders. `amount` is `sum(payments.amount)`, which doesn't depend on the method, so it still shows 708.00 and 146.00.
- **Fees:** the join to `raw_payment_methods` on a NULL method fails, so `coalesce(fee_rate, 0)` gives 0.00 fees for March and April.
- **Tests:** `accepted_values` on `stg_payments.payment_method` ignores NULLs. `not_null` on the `orders` method columns passes because the pivot returns 0, not NULL. Nothing checks that the method amounts add up to `amount`.

**Second problem, `models/daily_revenue.sql`:** it shows March at 622.00 against 708.00 in `orders`, and April at 139.00 against 146.00. The incremental filter only rebuilds the last 3 days relative to `max(order_date)`. Payments that landed later than that for older orders were never picked up, and the rows are never corrected. This is separate from the NULL method, and fixing staging won't repair it.

## 2. Fix

In `models/staging/stg_payments.sql`:

```sql
    select
        id as payment_id,
        order_id,
        -- ingestion moved from `payment_method` to `method`; old rows have both, new rows only `method`
        coalesce(payment_method, method) as payment_method,

        -- `amount` is currently stored in cents, so we convert it to dollars
        amount / 100 as amount

    from source
```

Then rebuild everything, including the incremental model:

```
dbt build --full-refresh
```

Also ask the ingestion owner to stop splitting the field. Pick one column and backfill it.

After the rebuild, check that March credit card volume is 334.00 against the processor statement. March and April fees should also be non-zero, and `daily_revenue` should match `orders` by month (708.00 and 146.00).

Optionally, widen the `daily_revenue` lookback or key the incremental on payment arrival rather than order date. Otherwise this will recur whenever payments land more than 3 days late.

## 3. Tests that would have caught it

**Staging, NULL method** (`models/staging/schema.yml`). `accepted_values` skips NULLs, so add:

```yaml
      - name: payment_method
        tests:
          - not_null
          - accepted_values: ...   # existing
```

This fails on the original code: 118 rows total, with the new-feed rows NULL.

**Singular test: method columns reconcile to `amount`** (`tests/assert_orders_method_amounts_sum_to_amount.sql`):

```sql
select order_id, amount,
       credit_card_amount + coupon_amount + bank_transfer_amount + gift_card_amount as method_total
from {{ ref('orders') }}
where amount is not null
  and abs(amount - (credit_card_amount + coupon_amount + bank_transfer_amount + gift_card_amount)) > 0.005
```

This catches NULL methods, and also any new method value that isn't in the `payment_methods` list.

**Singular test: `daily_revenue` reconciles to `orders`** (`tests/assert_daily_revenue_matches_orders.sql`):

```sql
select d.order_date, d.revenue, o.revenue as orders_revenue
from {{ ref('daily_revenue') }} d
join (select order_date, sum(amount) as revenue from {{ ref('orders') }} group by 1) o
  using (order_date)
where abs(d.revenue - o.revenue) > 0.005
```

This catches the stale incremental rows.

**Optional source-level check:** when both columns are populated they must agree, and at least one must be populated:

```sql
select * from {{ ref('raw_payments') }}
where coalesce(payment_method, method) is null
   or (payment_method is not null and method is not null and payment_method <> method)
```
