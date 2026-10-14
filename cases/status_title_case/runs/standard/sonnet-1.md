claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 15 s | USD 0.084 | 2026-10-15 | project ad0b16b | prompt 6cd8da3825cf

## 1. Root cause

The status values in `raw_orders` (the `seeds/raw_orders.csv` feed) are inconsistent in case. This week's rows arrive capitalised (`Completed`, `Placed`, `Shipped`, `Return_pending`), while older rows are lowercase. `stg_orders.sql` passes `status` through untouched.

`customers.sql` (the `customer_payments` CTE) then does an exact, case-sensitive match:

```sql
sum(case when orders.status = 'completed' then amount else 0 end)
```

That silently drops every `Completed` order. The output table confirms it:

| status | orders | amount |
|---|---|---|
| `completed` | 36 | 604.00 |
| `Completed` | 31 | 499.00 |

604.00 + 499.00 = **1103.00**, which is the ledger figure. `customer_lifetime_value` still sums to 1672.00 because it has no status filter, so only the completed measure is wrong.

The tests passed because someone widened the `accepted_values` lists to include the capitalised variants. This is in `models/schema.yml` (`orders.status`) and `models/staging/schema.yml` (`stg_orders.status`). That change turned the one test that would have flagged the drift into one that approves it.

## 2. Fix

Normalise the value once in staging, so every downstream model sees one canonical value.

`models/staging/stg_orders.sql`:
```sql
    select
        id as order_id,
        user_id as customer_id,
        ordered_at,
        cast(ordered_at as date) as order_date,
        lower(trim(status)) as status

    from source
```

Then tighten both `accepted_values` lists (`stg_orders.status` and `orders.status`) back to lowercase only:
```yaml
values: ['placed', 'shipped', 'completed', 'return_pending', 'returned']
```

After `dbt build`, `sum(completed_lifetime_value)` should be 1103.00. Other `orders.status` consumers will now see the merged lowercase values, for example `return_pending` goes from two buckets (1 and 1) to one (2).

If the upstream feed is the thing that changed, also ask the owner to fix it. The staging normalisation keeps the model correct either way.

## 3. Tests that would have caught it

**a) Strict `accepted_values`.** The lowercase-only list above would have failed on the first build with capitalised rows. This is the most direct fix.

**b) A reconciliation test**, `tests/assert_completed_ltv_matches_orders.sql`. It catches any future way the customer rollup could drift from orders, not only case:
```sql
with c as (
    select coalesce(sum(completed_lifetime_value), 0) as v from {{ ref('customers') }}
),
o as (
    select coalesce(sum(amount), 0) as v
    from {{ ref('orders') }}
    where lower(status) = 'completed'
)
select c.v as customers_total, o.v as orders_total
from c, o
where abs(c.v - o.v) > 0.005
```

Optionally, add a check that no status value has case variants (`count(distinct status) = count(distinct lower(status))`) on `raw_orders` as a source-level guard.

## Side note

The `customers` description in `schema.yml` documents `total_order_amount`, but the model outputs `customer_lifetime_value`. That mismatch is unrelated to this bug but worth cleaning up.
