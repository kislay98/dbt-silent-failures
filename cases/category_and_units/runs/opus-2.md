claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 46 s | USD 0.172 | 2026-10-14 | project 8ee5734 | prompt d0c0d63b3bb2

# Root cause: two raw-data changes in March that the code and tests didn't catch

## 1. Root cause

The gap is **136.00 in both numbers** (279 − 143 and 1103 − 967). So the whole problem is in March, and Jan/Feb (424 + 400) are correct. Two separate changes in the raw data cause it.

### a) `raw_orders.status`: "completed" became "delivered" from 2018-03-10

In the sample, no order on or after 2018-03-10 is `completed`. The 9 completed March orders all come before that date, and the 8 `delivered` orders all come after it. The shop now sends `delivered` where it used to send `completed`, and the admin counts `delivered` as completed revenue.

The code only counts the old value:
- `models/customers.sql`, in the `customer_payments` CTE: `case when orders.status = 'completed'`
- Anyone filtering `orders` on `status = 'completed'` has the same problem.

The tests didn't catch it because `'delivered'` was added to `accepted_values` in both `models/schema.yml` and `models/staging/schema.yml`. That silenced the test without changing the logic that uses the status.

### b) `raw_payments.amount`: some coupon payments are in dollars, not cents

`stg_payments.sql` divides every amount by 100. Some recent coupon rows are already in dollars, so they become amounts of a few cents:

| order | coupon_amount shown | real amount |
|---|---|---|
| 76 | 0.02 | 2.00 |
| 81 | 0.02 | 2.00 |
| 86 | 0.23 | 23.00 |
| 92 | 0.02 | 2.00 |
| 94 | 0.07 | 7.00 |
| 95 | 0.24 | 24.00 |

These are the only non-whole-dollar values in the whole output, and they are all coupons.

### Reconciliation

Delivered orders in March, with order 76 corrected to 2.00:

28 + 23 + 16 + 19 + 26 + 3 + 19 + **2** = **136.00**

- 143 + 136 = **279** ✓
- 967 + 136 = **1103** ✓

Both admin figures match exactly.

## 2. Fix

**Status:** normalize it once in staging so every downstream model works unchanged. In `models/staging/stg_orders.sql`:

```sql
    select
        id as order_id,
        user_id as customer_id,
        ordered_at,
        cast(ordered_at as date) as order_date,
        status as source_status,
        -- the shop renamed 'completed' to 'delivered' on 2018-03-10
        case when status = 'delivered' then 'completed' else status end as status

    from source
```

Then remove `'delivered'` from both `accepted_values` lists, so the next unexpected status fails the build instead of passing silently. Please confirm with the shop team that `delivered` really means `completed`. The admin totals strongly suggest it does.

**Coupon units:** fix this at the source.
- Have the ingestion job load coupon amounts in cents again.
- Backfill the affected rows: `update raw_payments set amount = amount * 100 where id in (...)`.
- Get the exact cutover point from the ingestion owner rather than guessing. Find the candidate rows with:

```sql
select * from raw_payments where payment_method = 'coupon' and amount % 100 <> 0;
```

  Note that a dollar-denominated coupon whose value is a multiple of 100 would not show up in this query.

Do not patch this in `stg_payments` with a heuristic like "coupon < 100 means dollars". It would silently mis-scale real small coupons.

After both fixes, run `dbt build --full-refresh -s daily_revenue+`. `daily_revenue` is incremental and only rebuilds the last 3 days, so without a full refresh the old March values stay in it.

## 3. Tests that would have caught it

**Units:** fail on implausibly small payments. In `models/staging/schema.yml`, on `stg_payments.amount`:

```yaml
      - name: amount
        tests:
          - dbt_utils.accepted_range:
              arguments:
                min_value: 1
                inclusive: true
```

If you don't use dbt_utils, use a singular test instead, `tests/assert_payment_amounts_whole_dollars.sql`:

```sql
-- every source amount in cents should be a whole number of dollars; fractional
-- results mean a row arrived in dollars and was divided by 100 again
select * from {{ ref('stg_payments') }} where amount < 1 or amount <> round(amount)
```

(Drop the `round` check if real prices can have cents.)

**Status:** the bug got through because the allowed list was edited without updating the logic. Tie the two together:
- Keep `accepted_values` strict, without `delivered`.
- Add a reconciliation test, `tests/assert_completed_revenue_reconciles.sql`. It fails if any status besides the known non-revenue ones carries payments that `customers` doesn't count as completed:

```sql
with by_status as (
    select status, sum(amount) as amount
    from {{ ref('orders') }}
    group by status
)
select * from by_status
where status not in ('completed', 'placed', 'shipped', 'return_pending', 'returned')
```

With the original code, the strict `accepted_values` would have failed on 2018-03-10, when `delivered` first appeared. The `accepted_range` test would have failed on 2018-03-20, when the first dollar-denominated coupon (order 76) arrived. A periodic check of `sum(completed_lifetime_value)` against the admin's completed total is also worth adding as a backstop.
