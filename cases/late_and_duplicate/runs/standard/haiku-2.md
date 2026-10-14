claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 50 s | USD 0.008 | 2026-10-14 | project ad0b16b | prompt 1c80550dffb3

## 1. Root cause

There are two separate defects. Together they explain all three numbers.

**a) Duplicate payment rows in the `raw_payments` seed (`seeds/raw_payments.csv`).** This is the data problem. `orders.amount` is the sum of each order's payments, so duplicated payment rows inflate it. `orders.sql` sums `payments.amount` per order, and `stg_payments` passes every row through. Order 1 shows 20.00 but was charged 10.00, which fits a second copy of a 10.00 payment. The gap is exactly 1917.00 − 1737.00 = **180.00**, which is the excess from the duplicates. I can't see the seed rows, so this is inferred from the totals and the order 1 example. The query in section 2 confirms it.

**b) `daily_revenue` never rewrites backfilled dates (`models/daily_revenue.sql`).** This is the model problem. On incremental runs it only rebuilds `order_date >= max(order_date) - 3 days` of the target table. Last night's February backfill is older than that window, so those rows are never rewritten. The 3-day window was meant for late-landing payments, not backfills. The dashboard is therefore a stale snapshot:

| Month | `sum(orders.amount)` | `daily_revenue.revenue` | Gap | Orders in `orders` | Orders in `daily_revenue` |
|---|---|---|---|---|---|
| Jan | 570 | 496 | 74 | 29 | 29 |
| Feb | 515 | 415 | 100 | 33 | 27 |
| Mar | 679 | 622 | 57 | 35 | 35 |
| Apr | 153 | 153 | 0 | 8 | 8 |
| **Total** | **1917** | **1686** | **231** | **105** | **99** |

The six missing February orders are the backfill that never reached `daily_revenue`. The other gaps are stale amounts on existing days.

Nothing caught either problem because all 32 tests check keys, nulls and accepted values. None of them compares totals to anything.

## 2. Fix

**Data (fix the source, then reseed):**

First, confirm the duplicates. This should return the rows to remove, and the excess should total 180.00:

```sql
select order_id, payment_method, amount, count(*) as copies
from raw_payments
group by order_id, payment_method, amount
having count(*) > 1
order by order_id;

select sum(amount) / 100.0 as total_dollars from raw_payments;  -- currently 1917.00
```

Remove the duplicate rows from `seeds/raw_payments.csv`. Before deleting, check with the processor IDs that none are legitimate repeat payments. Then run `dbt seed --select raw_payments`. If the backfill job regenerates that CSV nightly, fix it upstream too, or the duplicates will return.

**Model (`models/daily_revenue.sql`):** the table has only about 69 rows, so incremental buys nothing. Change it to a full table and remove the `is_incremental()` block:

```sql
{{ config(materialized='table') }}

with orders as (

    select * from {{ ref('orders') }}

),

final as (

    select
        order_date,
        count(*) as orders,
        sum(amount) as revenue

    from orders

    group by order_date

)

select * from final
```

Then run `dbt build --full-refresh --select daily_revenue` once to clear the stale rows, followed by a normal `dbt build`.

**Expected result:** `sum(orders.amount)`, `sum(daily_revenue.revenue)`, `sum(customers.customer_lifetime_value)` and `sum(region_revenue.revenue)` should all equal **1737.00**. `daily_revenue` should show 105 orders in total. `customers` and `region_revenue` need no code change because they read from `payments` and `orders`.

## 3. Test that would have caught this

The strongest check is a singular test that reconciles the warehouse to finance's settlement total. It fails for both defects. It needs the settlement total in the project, for example as a seed `raw_settlement_totals` with a single row, or as a hard-coded value to start:

```sql
-- tests/assert_payments_match_settlement.sql
with settled as (

    select sum(amount) as total from {{ ref('stg_payments') }}

),

settlement as (

    select sum(total_amount) as total from {{ ref('raw_settlement_totals') }}

)

select settled.total as settled_total, settlement.total as settlement_total
from settled
cross join settlement
where abs(settled.total - settlement.total) > 0.005
```

Two narrower tests are also worth adding, because they point at the specific defect:

```sql
-- tests/assert_daily_revenue_matches_orders.sql
-- Catches the stale incremental rows (would have failed by 231.00)
select o.total as orders_total, d.total as daily_total
from (select sum(amount) as total from {{ ref('orders') }}) o
cross join (select sum(revenue) as total from {{ ref('daily_revenue') }}) d
where abs(o.total - d.total) > 0.005
```

```sql
-- tests/assert_no_duplicate_payments.sql
-- Catches the duplicated payment rows (flags identical payments, so review any hits)
select order_id, payment_method, amount, count(*) as copies
from {{ ref('stg_payments') }}
group by order_id, payment_method, amount
having count(*) > 1
```

I haven't run any of this. The SQL is written against the schema and project structure you pasted, and the seed contents are inferred from the totals.
