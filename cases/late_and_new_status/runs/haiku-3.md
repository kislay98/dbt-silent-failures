claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 57 s | USD 0.008 | 2026-10-14 | project f769fdf+wk6ext | prompt c5114118dec9

## 1. Root cause

There are two separate bugs. Each one explains one of the reported mismatches exactly.

**a) Completed revenue: `delivered` is excluded from completed (`models/customers.sql`)**

`customers.completed_lifetime_value` only counts `status = 'completed'`:

```sql
sum(case when orders.status = 'completed' then amount else 0 end) as completed_amount
```

The admin also counts `delivered` as completed. The sample output shows the gap is exactly the delivered revenue:

| Month | completed | delivered | completed + delivered |
|---|---|---|---|
| Jan | 424 | 0 | 424 |
| Feb | 400 | 65 | 465 |
| Mar | 143 | 136 | 279 |
| **Total** | **967** | **201** | **1168** |

- The `customers` total of 967 is the completed-only sum.
- The admin's 1168 is completed plus delivered.
- March's 279 is 143 + 136.

`delivered` is listed as a valid status in `accepted_values`, so the status test passes. Nothing in the project says whether `delivered` is revenue, and the schema description ("status completed") is out of step with what finance expects.

**b) `daily_revenue` is missing late-arriving February orders (`models/daily_revenue.sql`)**

The incremental filter only rebuilds dates from `max(order_date) - 3 days` in the existing table:

```sql
where order_date >= (select max(order_date) - interval 3 day from {{ this }})
```

The table already has April data, so the window starts around April 6. Any February order that arrives later is never rebuilt. The output confirms this:

- The missing orders are 100–105, all February `delivered`, all with ids above 99. They were loaded in a later batch with older dates.
- Orders 100–105 total 65.0 (12 + 9 + 15 + 6 + 20 + 3), which is exactly 480 − 415.
- Their count is 6, which is exactly 33 − 27.

Every other month and date matches, so the bug is this one lookback window.

**Ruled out:** the `orders` fan-out through `raw_payment_methods`. If that seed had duplicate `payment_method` rows, order amounts would be inflated, but total revenue matches the admin in every other month. The seed data wasn't provided, so I can't fully verify this, but the totals are consistent with no fan-out.

## 2. Fix

**a) Count `delivered` as completed.** I used the admin's definition because finance treats it as correct. If `delivered` should not count, tell me and I'll revert this part.

```sql
-- models/customers.sql
sum(case when orders.status in ('completed', 'delivered') then amount else 0 end) as completed_amount
```

Also update the description in `models/schema.yml`:

```yaml
- name: completed_lifetime_value
  description: Total value (AUD) of a customer's orders with status completed or delivered
```

**b) Stop the incremental lookback from missing old orders.** The `orders` table is only about 100 rows, so incremental adds risk without a real benefit. Make `daily_revenue` a full rebuild:

```sql
-- models/daily_revenue.sql
{{
    config(
        materialized='table'
    )
}}

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

Then rebuild the existing table so February is corrected (`dbt run -s daily_revenue` is enough once it is a table, since it will be replaced). Expected result: February shows 33 orders and 480.

## 3. Tests that would have caught this

**a) Reconciliation of `daily_revenue` against `orders`.** This would have failed on the February gap immediately:

```sql
-- tests/assert_daily_revenue_matches_orders.sql
with orders_by_day as (

    select
        order_date,
        count(*) as orders,
        sum(amount) as revenue

    from {{ ref('orders') }}
    group by order_date

),

daily as (

    select * from {{ ref('daily_revenue') }}

)

select
    coalesce(o.order_date, d.order_date) as order_date
from orders_by_day o
full outer join daily d
    on o.order_date = d.order_date
where o.orders is distinct from d.orders
   or round(o.revenue, 2) is distinct from round(d.revenue, 2)
```

**b) Status classification check.** Nothing forced someone to decide whether `delivered` is revenue. Add a `status_groups` seed (`status`, `is_completed`) with one row per status, and a `relationships` or `not_null` test on `is_completed` from `stg_orders.status`. Then `customers.sql` should join to that mapping instead of hard-coding the list. This turns an undocumented business rule into a reviewed, tested one.

I haven't run any of this. The analysis is based on the files and sample output you provided, so the next step is to run `dbt build` against the real data. Two checks would confirm the diagnosis: `select count(*) from daily_revenue where order_date = '2018-02-03'` should return 0 before the fix and 1 after, and `select sum(completed_lifetime_value) from customers` should equal 1168 after the status change.
