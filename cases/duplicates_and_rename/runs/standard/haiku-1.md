claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 32 s | USD 0.006 | 2026-10-14 | project ad0b16b | prompt a85110595fb3

## 1. Root cause

**`jaffle_shop/models/staging/stg_payments.sql`** passes `payment_method` straight through from `raw_payments`, but the ingestion job now fills that column inconsistently. The raw table has two method columns:

- `payment_method`: NULL on many rows. Every sampled row from `order_id` 59 onward is NULL here (payment ids 86, 90, 93, 97, 111, 112, 114, 115).
- `method`: populated on every sampled row, including the ones where `payment_method` is NULL.

Where both columns are populated (payment ids 12 and 28), they agree.

The NULLs flow through the rest of the project:

- **`orders.sql`**: each `sum(case when payments.payment_method = '...' then amount else 0 end)` matches no rows for a NULL method, so all four method buckets are 0. `total_amount` sums `amount` regardless of method, which is why the March total (708) and April total (146) still look right.
- **Fees**: the join to `raw_payment_methods` on a NULL key finds no fee rate, so `coalesce(fee_rate, 0)` gives 0. That explains the 0.00 fees for March and April.
- **Jan and Feb** have `payment_method` populated, so they are correct.

**Why every test passed:**

- `accepted_values` on `stg_payments.payment_method` ignores NULLs.
- There is no `not_null` test on `stg_payments.payment_method`.
- The `not_null` tests on `orders.credit_card_amount` and the other buckets never fire, because `else 0` means those columns are never NULL.
- Nothing checks that the four buckets add up to `amount`.

I can't confirm from the pasted data that March credit card volume is 334. Run `select sum(amount) from raw_payments p join raw_orders o ... where method='credit_card' and month = March` (or the equivalent against `stg_payments`) to check it after the fix.

## 2. Fix

**Option A (minimal, in dbt):** in `jaffle_shop/models/staging/stg_payments.sql`, take the method from whichever column is populated:

```sql
coalesce(payment_method, method) as payment_method,
```

Before merging, confirm that `method` matches `payment_method` on every row where both are populated, and that no row has both NULL. If they disagree anywhere, stop and ask the ingestion owners which column is authoritative.

**Option B (upstream):** have the ingestion job populate `payment_method` from `method`, or rename the column. This is the cleaner long-term fix, but it depends on the ingestion team.

Then run `dbt build`. `orders`, `customers`, and `region_revenue` are tables and rebuild in full.

**Second issue, `daily_revenue`:** its `revenue` for March is 622 against 708 in `orders`, and April is 139 against 146. Order counts match exactly. The likely cause is the incremental window: it rebuilds only the last three days from the table's max `order_date`, so March days built before their payments landed are never refreshed. This is an inference from the numbers, not something I've confirmed. Fix it with:

```
dbt build -s daily_revenue --full-refresh
```

Then compare `sum(revenue)` by month against `orders.amount`. If the window is still too short for late payments, widen it or schedule a periodic full refresh.

## 3. Test that would have caught it

**a. `not_null` on the staging column**, in `jaffle_shop/models/staging/schema.yml`:

```yaml
  - name: stg_payments
    columns:
      - name: payment_method
        tests:
          - not_null
          - accepted_values: ...   # existing
```

This fails on the current data, because the sampled NULL rows are present.

**b. A singular test that the payment buckets reconcile to the order amount**, in `jaffle_shop/tests/assert_orders_payment_methods_sum_to_amount.sql`:

```sql
select order_id, amount,
       credit_card_amount + coupon_amount + bank_transfer_amount + gift_card_amount as paid
from {{ ref('orders') }}
where abs(amount - (credit_card_amount + coupon_amount + bank_transfer_amount + gift_card_amount)) > 0.005
```

This would have failed for every March and April order. It checks the property finance actually cares about, not just that each column is non-null.

**Aside:** `models/schema.yml` documents `total_order_amount` on `customers`, a column that doesn't exist (the model produces `customer_lifetime_value`). It's harmless to dbt but misleading, so fix it while you're there.
