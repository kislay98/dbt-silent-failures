claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 55 s | USD 0.241 | 2026-10-14 | project ad0b16b | prompt eb0b3a80ab67

# Two source-data problems, hidden by a widened test

## 1. Root cause

There are two problems in the raw data. Neither causes a test failure. Together they fully explain finance's numbers.

### a) `raw_orders.status` has a new value, `delivered`, which the shop counts as completed

- The `delivered` status shows up in March (for example, order 75 on 2018-03-18). There are 8 such orders.
- `customers.sql` only counts `status = 'completed'` toward `completed_lifetime_value`, so these orders are left out.
- The tests didn't catch this because `'delivered'` was added to the `accepted_values` list in both `models/staging/schema.yml` and `models/schema.yml`. The test that should have flagged the new value was widened to accept it.
- I'm inferring that `delivered` means completed from the numbers below. Please confirm with whoever owns the shop system that `delivered` is a rename of `completed` and not a separate lifecycle stage.

### b) Some `raw_payments.amount` values are in dollars, not cents

- `stg_payments.sql` divides every amount by 100. All the coupon rows are whole cents except a few small ones: `23` (order 86), `2` (order 76), and `7` (order 94).
- These few rows are really dollar amounts. They become $0.23, $0.02, and $0.07.
- That is why `orders` has fractional coupon totals (March 18.27, April 0.33), when every other amount is whole dollars.
- `daily_revenue` still has the old, correct values (March 622, April 139). It's incremental and only rebuilds the last 3 days, so most of its rows were built before these rows arrived.

### The numbers reconcile exactly

| | Value |
|---|---|
| `delivered` amount in `orders` | 134.02 |
| …with the 0.02 coupon corrected to 2.00 | **136.00** |
| March: 143 (completed) + 136 | **279** ✔ matches the admin |
| Since January: 967 + 136 | **1103** ✔ matches the admin |
| March coupon gap: 622 − 595.27 | 26.73 = 0.27 → 27 ✔ |
| April coupon gap: 139 − 106.33 | 32.67 = 0.33 → 33 ✔ |

Both fixes are needed. Fixing only the status gives 277.02, not 279.

## 2. Fix

**Preferred:** fix this at ingestion. Ask the owner of the ingestion job to reload the affected coupon rows in cents. Then normalise the status in staging.

`models/staging/stg_orders.sql`:
```sql
        cast(ordered_at as date) as order_date,
        -- the shop renamed 'completed' to 'delivered' in March 2018
        case when status = 'delivered' then 'completed' else status end as status
```

`models/staging/stg_payments.sql`, as a stopgap until the source is reloaded:
```sql
        -- `amount` is stored in cents, except some coupon rows that arrive in dollars
        case
            when payment_method = 'coupon' and amount % 100 <> 0 then amount
            else amount / 100
        end as amount
```
This heuristic misses any dollar-unit coupon of $100 or more. The current amounts are all well under that, but it's still a guess, so the source reload is the real fix. To see exactly which rows are affected:
```sql
select * from raw_payments where amount % 100 <> 0;
```

Then remove `'delivered'` from both `accepted_values` lists. Rebuild with a full refresh so the stale incremental rows in `daily_revenue` are replaced:
```
dbt build --full-refresh
```

## 3. Tests that would have caught it

**Keep `accepted_values` strict.** Never add a value to make the test pass. Instead, test the raw status column so new upstream values fail loudly:
```yaml
seeds:   # or sources:, once raw_orders comes from the ingestion job
  - name: raw_orders
    columns:
      - name: status
        tests:
          - accepted_values:
              arguments:
                values: ['placed', 'shipped', 'completed', 'return_pending', 'returned']
```

**Unit sanity check on payments.** `tests/assert_payment_amounts_in_whole_cents_dollars.sql`:
```sql
-- prices are whole dollars, so cent amounts must be multiples of 100
select * from {{ ref('raw_payments') }}
where amount % 100 <> 0
```

**Reconciliation test.** This one would have flagged the March/April gap (622 vs 595.27) immediately, and it also guards against `daily_revenue` drifting from `orders`. `tests/assert_daily_revenue_matches_orders.sql`:
```sql
with o as (
    select order_date, sum(amount) as revenue
    from {{ ref('orders') }}
    group by 1
)
select o.order_date, o.revenue, d.revenue as daily_revenue
from o
full outer join {{ ref('daily_revenue') }} d using (order_date)
where abs(coalesce(o.revenue, 0) - coalesce(d.revenue, 0)) > 0.005
```

One unrelated cleanup: `models/schema.yml` documents a `customers.total_order_amount` column, but the model actually outputs `customer_lifetime_value`.
