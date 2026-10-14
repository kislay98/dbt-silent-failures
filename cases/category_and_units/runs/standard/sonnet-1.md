claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 65 s | USD 0.148 | 2026-10-14 | project ad0b16b | prompt eb0b3a80ab67

## Root cause

The 136.00 gap is the whole difference. 1103.00 − 967.00 = 279.00 − 143.00 = 136.00, and all of it sits in March.

**1. `delivered` orders are not counted as completed (main cause).**
- In `raw_orders` (seed `raw_orders`, surfaced via `stg_orders.sql`), 8 orders now carry a status of `delivered`. They total 134.02 in `orders`, all in March.
- `customers.sql` only counts `orders.status = 'completed'` in `completed_amount`, so `completed_lifetime_value` and the March completed figure leave them out.
- The tests didn't catch it because `delivered` was added to the `accepted_values` lists in `models/schema.yml` and `models/staging/schema.yml`. A test that only checks for allowed values can't see that the meaning of a status changed.
- Finance clearly counts delivered as completed. 134.02 + 1.98 = 136.00 exactly.

**2. Some `raw_payments` amounts are in dollars, not cents (the missing 1.98).**
- `stg_payments.sql` divides every amount by 100, which assumes cents. All the other amounts are multiples of 100, but some coupon rows are not: id 98 (order 86) is 23, id 86 (order 76) is 2, and there are similar rows for order 94.
- Those rows become $0.23, $0.02 and $0.07, which is why the coupon column in `orders` is the only one with fractional values (18.27 in March, 0.33 in April).
- `daily_revenue` is stale and still holds the whole-dollar values: March is 622.00 against 595.27 in `orders`, and April is 139.00 against 106.33. The gaps are 26.73 and 32.67, which are exactly 27 − 0.27 and 33 − 0.33.
- The two delivered coupon rows at 2 each account for the missing 1.98.

I haven't seen the full seed, so the exact set of affected payment rows is inferred from these totals.

`daily_revenue.sql` has two further problems. It never filters on status. Its 3-day window is based on `order_date`, so older rows are never refreshed when payments are restated.

## Fix

**Status.** Define the completed set once and use it in `customers.sql`:

```sql
-- dbt_project.yml
vars:
  completed_statuses: ['completed', 'delivered']
```
```sql
-- customers.sql, customer_payments CTE
sum(case when orders.status in (
    {%- for s in var('completed_statuses') -%}'{{ s }}'{{ ',' if not loop.last }}{%- endfor -%}
) then amount else 0 end) as completed_amount
```

Confirm with finance that `delivered` should count as completed. The arithmetic says yes.

**Units.** Fix this at the ingestion job and reload `raw_payments`. As a one-off repair of the seed data, check the rows first, then update:

```sql
select * from raw_payments where amount % 100 <> 0;            -- inspect first
update raw_payments set amount = amount * 100 where amount % 100 <> 0;
```

Then run `dbt build --full-refresh -s daily_revenue+ customers+ region_revenue+` so the stale incremental rows are rebuilt. After that, `delivered` plus `completed` should give 1103.00 since January and 279.00 in March.

## Tests that would have caught it

```sql
-- tests/assert_payment_amounts_in_cents.sql
select id, payment_method, amount from {{ ref('raw_payments') }}
where amount % 100 <> 0
```

```sql
-- tests/assert_daily_revenue_reconciles_to_orders.sql
select 1
where abs((select sum(revenue) from {{ ref('daily_revenue') }})
        - (select sum(amount)  from {{ ref('orders') }})) > 0.005
```

```sql
-- tests/assert_customers_completed_matches_orders.sql
select 1
where abs((select sum(completed_lifetime_value) from {{ ref('customers') }})
        - (select sum(amount) from {{ ref('orders') }}
           where status in ('completed','delivered'))) > 0.005
```

To stop status drift, put the status-to-completed mapping in a seed. Add a `relationships` test from `stg_orders.status` to that seed, so a new status fails until someone decides whether it counts as completed.
