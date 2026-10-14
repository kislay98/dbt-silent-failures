claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 58 s | USD 0.240 | 2026-10-14 | project ad0b16b | prompt 2b2290cfef14

Two separate problems cause what finance and ops are seeing. Every test passes because the tests were either widened to fit the new data or don't compare models against each other.

## 1. Root causes

### A. Completed revenue: a new `delivered` status that `customers.sql` doesn't count

The numbers match exactly:

| | completed (dbt) | + delivered | = admin |
|---|---|---|---|
| since January | 967.00 | 201.00 | **1168.00** |
| March | 143.00 | 136.00 (implied) | **279.00** |

`orders` has 14 orders with status `delivered` totalling 201.00. That status isn't in the original jaffle_shop status set. The shop has started marking finished orders as `delivered`, apparently around March, and the admin counts them as completed revenue.

- **`models/customers.sql`** hard-codes `orders.status = 'completed'`, so delivered orders count toward `customer_lifetime_value` but not `completed_lifetime_value`.
- **`models/schema.yml`** and **`models/staging/schema.yml`** both had `'delivered'` added to their `accepted_values` lists. That was the moment the new status was noticed, and the tests were updated without the model logic.

### B. February in `daily_revenue`: late-loaded orders outside the incremental window

`daily_revenue` is short by 6 orders and 65.00, only in February. Every other month matches `orders` exactly. **`models/daily_revenue.sql`** only rebuilds `order_date >= max(order_date) - 3 days` of its own table. Six orders with February `ordered_at` timestamps reached `raw_orders` after the table had already moved past early March, so they fall outside the lookback window and are never inserted.

This can't be late payments. A late payment would leave `count(*)` unchanged, because every order is counted whether or not it has payments, but the order count itself is off by 6. It also can't be orders moved between months, since January, March and April all match.

You can confirm both causes with:

```sql
-- A: delivered by month should be 136.00 in March and 201.00 in total
select date_trunc('month', order_date) m, count(*), sum(amount)
from orders where status = 'delivered' group by 1 order by 1;

-- B: the 6 February orders/days missing or stale in daily_revenue
select o.order_date, count(*) as orders, sum(o.amount) as revenue, d.orders as dr_orders, d.revenue as dr_revenue
from orders o left join daily_revenue d on d.order_date = o.order_date
where o.order_date between '2018-02-01' and '2018-02-28'
group by o.order_date, d.orders, d.revenue
having d.orders is distinct from count(*) or d.revenue is distinct from sum(o.amount)
order by 1;
```

## 2. Fix

**A.** Define "completed" once, in staging, and use it everywhere.

`dbt_project.yml`:
```yaml
vars:
  completed_statuses: ['completed', 'delivered']
  other_statuses: ['placed', 'shipped', 'return_pending', 'returned']
```

`models/staging/stg_orders.sql`, in `renamed`:
```sql
        status,
        status in ('{{ var("completed_statuses") | join("', '") }}') as is_completed
```

`models/customers.sql`:
```sql
        sum(case when orders.is_completed then amount else 0 end) as completed_amount
```

`models/orders.sql`: add `orders.is_completed,` after `orders.status,`. Finance can then filter on `is_completed` instead of `status = 'completed'`, which is how they got 143.00 for March.

Before relying on this, confirm with finance that `delivered` means completed and not some in-between state. The figures match the admin exactly, but that only shows how the admin counts it.

**B.** Rebuild the table now:
```
dbt build --full-refresh -s daily_revenue
```
Then stop it from drifting again. My recommendation is to make it a plain `table`. It's 69 rows aggregated from `orders`, which is already fully rebuilt on every run, so being incremental buys nothing. In `daily_revenue.sql`, replace the config block with `{{ config(materialized='table') }}` and drop the `is_incremental()` filter. If it has to stay incremental, filter on a load timestamp from ingestion (`raw_orders` doesn't have one today) and schedule a periodic `--full-refresh`. Widening the 3-day window only narrows the gap.

## 3. Tests that would have caught it

**A.** Make every new status a deliberate classification. Put this in `tests/assert_order_statuses_classified.sql`:
```sql
select status, count(*) as orders
from {{ ref('stg_orders') }}
where status not in (
    {%- for s in var('completed_statuses') + var('other_statuses') %}
    '{{ s }}'{% if not loop.last %},{% endif %}
    {%- endfor %}
)
group by status
```
This replaces the hand-maintained `accepted_values` lists on `status`. When `delivered` showed up, the build would have failed until someone decided which list it belongs in, and that same list drives `is_completed`. With two independent lists, the test got updated and the model didn't.

**B.** Reconcile `daily_revenue` against its source. Put this in `tests/assert_daily_revenue_matches_orders.sql`:
```sql
with expected as (
    select order_date, count(*) as orders, sum(amount) as revenue
    from {{ ref('orders') }}
    group by order_date
)

select
    coalesce(e.order_date, d.order_date) as order_date,
    e.orders as expected_orders, d.orders as actual_orders,
    e.revenue as expected_revenue, d.revenue as actual_revenue
from expected e
full outer join {{ ref('daily_revenue') }} d
    on e.order_date = d.order_date
where e.orders is distinct from d.orders
   or abs(coalesce(e.revenue, 0) - coalesce(d.revenue, 0)) > 0.005
```
This fails today on the February dates. The existing `unique`/`not_null` tests on `daily_revenue` can't detect missing rows.

One unrelated issue: `schema.yml` documents a `total_order_amount` column on `customers`, but the model outputs `customer_lifetime_value`.
