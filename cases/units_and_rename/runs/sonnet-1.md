claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 34 s | USD 0.079 | 2026-10-14 | project bef6b69 | prompt aaad08d98757

## 1. Root cause

**`models/staging/stg_payments.sql`** reads only `payment_method` from `raw_payments`. Starting in March, the ingestion job loads the method into a new column, `raw_payments.method`, and leaves `payment_method` NULL. The raw table now has both columns.

- Every March and April row in `stg_payments` has `payment_method = NULL`, as in the sample for payments 66, 67, 86, 105 and 106.
- `orders.sql` pivots with `case when payment_method = '...'`, so NULL matches nothing. Each method column is 0.00, while `sum(amount)` still counts the row, which gives 595.27 and 106.33.
- Nothing failed because:
  - `accepted_values` ignores NULLs.
  - There is no `not_null` test on `stg_payments.payment_method`.
  - The `not_null` tests on `orders.*_amount` pass because the amounts are 0, not NULL.

**The settlement gap is a second problem I'm inferring.** The shortfalls are 622.00 − 595.27 = 26.73 and 139.00 − 106.33 = 32.67. Both are exactly 0.99 × a whole number (27 and 33). That is the pattern you get when rows whose `amount` is already in whole dollars are divided by 100 in `stg_payments` (`amount / 100`).

- The tiny order totals (0.02, 0.23, 0.07, 0.24) fit this.
- April's 33 equals marketing's coupon ledger for April exactly.
- March's 27 is less than the 45 ledger. The other 18 may be coupon rows that are still in cents, but I can't confirm that from what's here.

My best guess is that the new feed sends some rows, probably coupons, in dollars instead of cents. Check this before changing the scaling, because I haven't seen the raw rows:

```sql
select date_trunc('month', o.ordered_at) m, coalesce(payment_method, method) pm,
       count(*), sum(amount) raw_sum
from raw_payments p join raw_orders o on o.id = p.order_id
where o.ordered_at >= '2018-03-01'
group by 1, 2 order by 1, 2;
```

Compare the `raw_sum` per method with the ledger and settlement. If it matches, whole-dollar rows are confirmed.

## 2. Fix

**Method column.** In `stg_payments.sql`:

```sql
payment_method as source_payment_method,  -- drop if unused
coalesce(payment_method, method) as payment_method,
```

Use a plain `coalesce(payment_method, method) as payment_method` in the select, and don't also select the raw `payment_method` column.

**Amount scale.** Once the query confirms which rows are in dollars, handle them explicitly. For example, if it's coupons in the new feed:

```sql
case
    when payment_method is null and method = 'coupon' then amount        -- new feed: whole dollars
    else amount / 100
end as amount
```

Don't ship this rule until the query confirms it. If the unit really changed in the feed, also ask the ingestion owner to fix it at the source or document it.

**Rebuild.** Run `dbt build --full-refresh`. `daily_revenue` is incremental and only rewrites the last 3 days, so it would keep the old values for earlier dates.

**Afterwards, check that the results reconcile:**
- March total is 622.00.
- April total is 139.00.
- Coupons are 45.00 and 33.00.

## 3. Tests

**Not-null and allowed values.** In `models/staging/schema.yml`:

```yaml
- name: payment_method
  tests:
    - not_null
    - accepted_values:
        arguments:
          values: ['credit_card', 'coupon', 'bank_transfer', 'gift_card']
```

**Method columns must add up to the total.** `tests/assert_orders_method_amounts_sum_to_amount.sql`:

```sql
select order_id
from {{ ref('orders') }}
where abs(credit_card_amount + coupon_amount + bank_transfer_amount
          + gift_card_amount - amount) > 0.005
```

This fails on every March and April order today.

**Reconciliation to the settlement, and schema drift.** Add a monthly reconciliation singular test against a seed of settlement totals, and a source-level check that fails when `payment_method` is NULL for a share of recent rows. The first test above would already have caught the NULL method; the reconciliation catches the scale problem.
