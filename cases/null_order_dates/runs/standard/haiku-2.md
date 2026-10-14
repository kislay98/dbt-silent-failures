claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 43 s | USD 0.008 | 2026-10-14 | project ad0b16b | prompt 3b339a6e8808

## 1. Root cause

**The data, not the SQL.** Eight rows in `raw_orders` have `ordered_at = NULL`. The ingestion job loaded them without timestamps. They account for the entire March gap and nothing else:

- `orders` by month has a NULL-date bucket of 8 orders and 149.00.
- March is 27 orders and 473.00 in `orders`, versus 35 and 622.00 in the dashboard and shop admin. 27 + 8 = 35 and 473 + 149 = 622.
- January, February, and April have no NULL-dated orders, which is why they agree.

The NULL dates then flow through the models without any error:

- **`stg_orders.sql`** casts `ordered_at` to `order_date` with no guard, so the NULL passes straight through.
- **`orders.sql`** passes the row through with `order_date = NULL`. All 99 orders are still present (the 99 total matches across `raw_orders`, `stg_orders`, `orders`, and `customers`), but 8 have no date.
- **`customers.sql`** uses `min(order_date)`, which ignores NULLs. A customer whose only order has no date has `first_order = NULL` while `number_of_orders` still counts it. This is the "ordered in March but no first order date" symptom.
- **`daily_revenue.sql`** is why the dashboard is still right and will never self-correct. The incremental filter `where order_date >= max(order_date) - 3 days` silently drops NULL-dated rows on every run. The March figures were written before those timestamps went missing, and March is never rebuilt because it falls outside the 3-day window. The `not_null` test on `daily_revenue.order_date` passes because no NULL group is ever inserted. A full refresh would expose the problem by inserting a NULL group and failing that test.

**Why no test failed:** `schema.yml` declares no `not_null` on `orders.order_date` or on `stg_orders.ordered_at`. The only date checks are on `daily_revenue`, and the incremental filter keeps NULLs out of it.

I haven't run anything. This diagnosis is from the outputs you pasted. The 8-order, 149.00 match is exact, but confirm with the query in step 1.

## 2. Fix

**Step 1: confirm and restore the source data.**

```sql
select id, user_id, status, ordered_at from raw_orders where ordered_at is null;
```

Expect 8 rows. Get the real `ordered_at` values from the source system and fix the ingestion job so it stops writing NULLs for these rows. Reload `raw_orders`. If the source genuinely has no timestamp for these orders, stop and decide with finance how to date them. Don't impute silently.

Note that the nightly build doesn't run `dbt seed`, so `ref('raw_orders')` resolves to whatever the last seed or load put there. Make sure the reload is the version the models read.

**Step 2: rebuild the stale dashboard.** Once the data is fixed, rebuild `daily_revenue` from scratch so March is recomputed:

```
dbt build --select daily_revenue --full-refresh
```

**Step 3: optional model hardening.** The incremental filter should not silently drop rows. If you want the build to stop when dates are missing, add the test below. Don't change `customers.sql` for this, because `first_order` becomes correct once the dates are present.

## 3. Tests that would have caught this

Add to `jaffle_shop/models/staging/schema.yml`:

```yaml
  - name: stg_orders
    columns:
      - name: ordered_at
        tests:
          - not_null
```

Add to `jaffle_shop/models/schema.yml`, under `orders` → `order_date`:

```yaml
      - name: order_date
        tests:
          - not_null
```

The `not_null` on `stg_orders.ordered_at` fails at the source, which is the most direct check. The `not_null` on `orders.order_date` covers the model.

To catch the stale-dashboard problem itself, add a singular test that fails whenever `daily_revenue` disagrees with `orders` on any date. It would fail today on the March dates.

`jaffle_shop/tests/daily_revenue_matches_orders.sql`:

```sql
with from_orders as (

    select
        order_date,
        count(*) as orders,
        sum(amount) as revenue

    from {{ ref('orders') }}
    where order_date is not null
    group by order_date

)

select
    coalesce(d.order_date, o.order_date) as order_date

from {{ ref('daily_revenue') }} as d
full outer join from_orders as o
    on d.order_date = o.order_date

where d.orders is distinct from o.orders
    or abs(coalesce(d.revenue, 0) - coalesce(o.revenue, 0)) > 0.005
```

Note that the overall totals reconcile today (both sum to 1,672.00) because the stale dashboard still contains the 149.00. A per-date comparison is what exposes it, which is why this test is needed in addition to the `not_null` checks.
