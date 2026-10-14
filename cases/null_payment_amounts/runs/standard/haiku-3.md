claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 43 s | USD 0.007 | 2026-10-14 | project ad0b16b | prompt b1e95fc444b5

## 1. Root cause

The likely root cause is **NULL payment amounts in `raw_payments`**, which the ingestion job loads. `stg_payments.sql` passes `amount / 100` straight through, so NULL stays NULL. `orders.sql` then aggregates with `sum(payments.amount)`, and SQL `sum()` silently skips NULLs. An order with one good payment and one NULL payment gets a total that is short by the NULL payment's value.

The data points to this:

- **Order 25:** `raw_payments` has id 30 (`order_id` 25, `coupon`) with `amount` NULL. Its other payment, id 28 (bank transfer, 2000 cents = 20.00), is not NULL. So `orders.amount` shows only the non-NULL part, and the 16.00 gap between the settlement (58.00) and the model (42.00) matches a missing coupon payment of that size.
- **Jan–Mar gap:** `daily_revenue` sums to 1533.00, matching the settlement. `orders` sums to 1468.00. The 65.00 difference is the total value of the NULL-amount payments in those months. Other NULL rows exist (e.g. id 55, credit_card, order 49).
- **Why the tests pass:** `not_null` on `orders.amount` only fails when *every* payment for an order is NULL. Partial NULLs produce a non-null sum, so the test never fires.
- **Why `daily_revenue` still agrees with the settlement:** it is incremental and only rebuilds the last three days. Jan–Mar rows were written before the amounts went NULL and have not been rebuilt since. Its agreement with the settlement is coincidental staleness, not a correct calculation. Once the window moves, it would drift too.

I inferred the 65.00 gap from the numbers, not from a query over the NULL rows. Confirm with:

```sql
select id, order_id, payment_method, amount
from raw_payments
where amount is null
  and order_id in (select order_id from stg_orders where order_date between '2018-01-01' and '2018-03-31');
```

The NULL rows should account for the full 65.00 once the settlement values are attached.

**Files involved:**
- `raw_payments` (data, loaded by the ingestion job): NULL `amount` values
- `models/staging/stg_payments.sql`: passes NULLs through without checking
- `models/orders.sql`: `sum(payments.amount)` drops NULLs silently
- `models/daily_revenue.sql`: 3-day incremental window never corrects history

## 2. Fix

**Data (required):** Restore the missing `raw_payments.amount` values from the processor settlement file, then tell the ingestion job to reject NULL amounts instead of loading them.

**Model (`models/staging/stg_payments.sql`):** Leave the value as-is so NULLs surface. Do not `coalesce` to 0, which would hide the problem. No change is needed here beyond the test below.

**Model (`models/daily_revenue.sql`):** The 3-day incremental window cannot correct historical data. The table is small (69 rows), so I'd drop the incremental config and make it a plain table:

```sql
{{ config(materialized='table') }}
```

Then do a one-time rebuild so the stale January–March rows are corrected:

```
dbt build --select daily_revenue --full-refresh
```

If you want to keep it incremental, widen the window to cover every date that could still change, or rebuild Jan–Mar explicitly.

## 3. Tests that would have caught this

**Not-null on the raw amount** (`models/staging/schema.yml`). This fails on the current data:

```yaml
  - name: stg_payments
    columns:
      - name: amount
        tests:
          - not_null
```

**Reconciliation between the layers** (`tests/assert_revenue_reconciles.sql`). This catches any drift between `daily_revenue` and `orders`. It would also have failed on the current data (1672.00 vs 1607.00 total):

```sql
with orders as (
    select sum(amount) as total from {{ ref('orders') }}
),

daily as (
    select sum(revenue) as total from {{ ref('daily_revenue') }}
)

select
    orders.total as orders_total,
    daily.total as daily_revenue_total
from orders
cross join daily
where abs(orders.total - daily.total) > 0.005
```

A reconciliation check against the external settlement total would catch this too, but it needs the settlement data loaded into the warehouse.

**Side note:** `models/schema.yml` documents a `total_order_amount` column on `customers` that the model does not produce. It's documentation drift, not the cause here, but it's worth fixing.
