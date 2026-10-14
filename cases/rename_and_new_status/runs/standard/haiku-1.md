claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 29 s | USD 0.006 | 2026-10-15 | project ad0b16b | prompt c4a4ba955988

## 1. Root cause

**`jaffle_shop/models/staging/stg_orders.sql` reads the wrong column.** It selects `status` from `raw_orders`, but the raw table also has `order_status`. The two columns are not interchangeable: `status` is NULL for 43 of the 99 orders, and `order_status` carries the value for those rows.

- The dbt output confirms this. In `orders`, 43 orders have `status = NULL` with 761.00 of amount, and `completed` totals only 50 orders / 824.00. That 824.00 is exactly `sum(customers.completed_lifetime_value)`.
- The admin total of 1103.00 minus 824.00 is **279.00**, which is the March figure you mentioned. This reconciles only if those 279.00 are orders with `status IS NULL` and `order_status = 'completed'`. That is the hypothesis I'd confirm first (see the check below).
- The NULL rows in the sample are concentrated in March and April, so the ingestion job probably began writing the status to `order_status` (or stopped writing `status`) around March. The raw data is the trigger, but the model is what turns it into wrong revenue, since it silently drops the value.

**Why every test passes:** `accepted_values` in both `schema.yml` files ignores NULLs, and there is no `not_null` test on `status` anywhere. The `accepted_values` check on `status` therefore never sees the missing rows. Also, `order_status` contains `delivered`, which is not in the accepted list. That value would fail a test, but the test runs on `status`, where those rows are NULL, so it never sees them either.

**Check to run first** (confirms the hypothesis before changing anything):

```sql
select
    status is null as status_missing,
    order_status,
    count(*) as orders,
    round(sum(ordered_at is not null), 0) as dated,
    sum(case when status is not null and status <> order_status then 1 else 0 end) as disagreeing
from raw_orders
group by 1, 2
order by 1, 2;
```

You should see `status_missing = true` with `order_status = 'completed'` and a total near 279.00 of amount when joined to `raw_payments`. Also confirm that `status` and `order_status` never disagree when both are non-null (the `disagreeing` column should be 0).

## 2. Fix

**Stage the status from whichever column is populated, and flag anything unknown rather than dropping it.** For the status to use, `order_status` is the newer field and has `delivered`, which the model's accepted list doesn't include. I wouldn't guess what `delivered` maps to. It may be `shipped`, or it may be a separate state that finance counts differently. Confirm with the ingestion owner before deciding.

Interim fix in `jaffle_shop/models/staging/stg_orders.sql`:

```sql
select
    id as order_id,
    user_id as customer_id,
    ordered_at,
    cast(ordered_at as date) as order_date,
    coalesce(status, order_status) as status

from source
```

This keeps existing non-NULL `status` values and fills the NULLs from `order_status`. Once `delivered` is mapped (or the accepted list is updated to include it), the `accepted_values` test will pass for the right reason.

After the change, rebuild `customers` and `orders`. They are `table` models, so a plain `dbt run --select stg_orders+` is enough (`daily_revenue` does not use status and needs no rebuild). Expected result if the hypothesis holds: `sum(completed_lifetime_value)` becomes 1103.00 and the completed order count rises by 279.00 worth of amount. `sum(amount)` stays at 1672.00.

## 3. A test that would have caught this

Add a test that compares the staged status against the source, so it fails whenever they diverge. Put it in `jaffle_shop/tests/assert_stg_orders_status_matches_source.sql`:

```sql
select
    r.id as order_id,
    r.status,
    r.order_status,
    s.status as staged_status
from {{ ref('raw_orders') }} r
join {{ ref('stg_orders') }} s
    on r.id = s.order_id
where s.status is distinct from coalesce(r.status, r.order_status)
```

This test fails on any NULL that the staging model didn't fill, and it would have failed on the March data.

Also add a `not_null` test on `status` in `jaffle_shop/models/staging/schema.yml` and in `jaffle_shop/models/schema.yml`, because `accepted_values` alone can't catch NULLs. Once `order_status` is staged in its own column, add `accepted_values` on `order_status` with the same list plus any new values, so `delivered` fails loudly.
