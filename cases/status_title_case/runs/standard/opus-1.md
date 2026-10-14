claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 19 s | USD 0.169 | 2026-10-15 | project ad0b16b | prompt 6cd8da3825cf

## 1. Root cause

**The data:** `seeds/raw_orders.csv` now contains the `status` values in mixed case. Some rows are `completed`, `returned` and `return_pending`. Others are `Completed`, `Placed`, `Shipped` and `Return_pending`. This comes straight through from `raw_orders`, as the sample shows (e.g. order 63 `Completed`, order 16 `completed`). `stg_orders.sql` passes `status` through untouched.

**The code that breaks:** `models/customers.sql`, in the `customer_payments` CTE:

```sql
sum(case when orders.status = 'completed' then amount else 0 end) as completed_amount
```

In DuckDB this comparison is case-sensitive, so the 31 `Completed` orders are dropped.

**The numbers confirm it** (from the `status` breakdown of `orders`):

| status | orders | amount |
|---|---|---|
| `completed` | 36 | 604.00 ← what `customers` reports |
| `Completed` | 31 | 499.00 ← silently excluded |
| **total** | 67 | **1103.00** ← the finance ledger |

`sum(orders.amount)` is still 1672.00 because it doesn't filter on status.

**Why every test passed:** the `accepted_values` tests on `status` in `models/schema.yml` and `models/staging/schema.yml` were widened to accept both `'completed'` and `'Completed'` (and the other capitalized variants). Instead of flagging the bad feed, the tests accepted it.

## 2. Fix

Normalize the value once in staging, so every downstream model sees a single spelling.

`models/staging/stg_orders.sql`:

```sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,
        ordered_at,
        cast(ordered_at as date) as order_date,
        lower(trim(status)) as status

    from source

)
```

`customers.sql` can stay as it is. After a rebuild, `sum(completed_lifetime_value)` will be 1103.00. `orders.status` will also become all lowercase, which fixes any other consumer that filters on status.

You should also raise the change with the owner of the orders feed. If the capitalization is unintentional, fix it at the source too.

## 3. Tests that would have caught it

**a) Restore strict `accepted_values`.** Replace the widened lists in both `models/staging/schema.yml` (`stg_orders.status`) and `models/schema.yml` (`orders.status`):

```yaml
      - name: status
        tests:
          - accepted_values:
              arguments:
                values: ['placed', 'shipped', 'completed', 'return_pending', 'returned']
```

If this had been on the raw seed (or on staging before the `lower()` fix), it would have failed this week's build. After the fix it guards against regressions. If you'd rather fail loudly on any upstream change, put the strict test on the `raw_orders` seed as well.

**b) A reconciliation test** that checks the derived metric against its source. Save it as `tests/assert_completed_ltv_reconciles_to_orders.sql`:

```sql
-- completed revenue in customers must equal completed order amounts, regardless of status casing
with c as (
    select coalesce(sum(completed_lifetime_value), 0) as v from {{ ref('customers') }}
),
o as (
    select coalesce(sum(amount), 0) as v
    from {{ ref('orders') }}
    where lower(status) = 'completed'
)
select c.v as customers_completed, o.v as orders_completed
from c, o
where abs(c.v - o.v) > 0.01
```

This test would have returned a row (604 vs 1103) and failed the build. It also catches other causes of the same symptom, such as a changed filter or a broken join.

A minor unrelated issue: `models/schema.yml` documents a column `total_order_amount` on `customers`, but the model outputs `customer_lifetime_value`. You should rename the entry in the docs.
