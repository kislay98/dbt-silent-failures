claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 29 s | USD 0.005 | 2026-10-14 | project bef6b69 | prompt aaf31b99f700

## 1. Root cause

**Data:** In the warehouse, `raw_orders` has `ordered_at = NULL` for eight March orders, ids 66 through 73. Their `status` is populated, so the rows loaded, but the timestamp did not. The `orders` output confirms it: the NULL-date bucket has 8 orders and 149.00, and 27 + 8 = 35 orders and 473.00 + 149.00 = 622.00, which matches the admin and dashboard totals. The eight orders are real March orders whose date was lost on load. The ingestion job is the likely source (a contiguous block of ids suggests a batch load problem), but the logs here don't show it, so I'd confirm against the source system and the ingestion logs for that batch.

**Why the build stays green:** Nothing in the project treats a missing date as an error, so the NULLs flow through silently:

- **`models/staging/stg_orders.sql`** passes `ordered_at` through and casts it to `order_date` without checking it. A NULL becomes a NULL date.
- **`models/schema.yml`** (the `orders` block) has no test on `order_date`, and the staging `schema.yml` has none on `ordered_at`. Only `amount` and the payment columns are `not_null`.
- **`models/orders.sql`** left-joins payments and keeps the NULL-dated orders, so `orders` has 35 March orders but only 27 with a date.
- **`models/customers.sql`** uses `min(order_date)`, which ignores NULLs. Customers 19 and 36 have only NULL-dated orders, so `first_order` and `most_recent_order` are NULL while `number_of_orders` still counts them (1 each).
- **`models/daily_revenue.sql`** is incremental and filters `where order_date >= (select max(order_date) - interval 3 day ...)`. A NULL date never satisfies that comparison, so these orders never enter the table. That is why the `not_null` test on `daily_revenue.order_date` passes. The rows are excluded, not NULL.

**Why the dashboard still shows 622:** The model only rebuilds the last three days, so March rows written before the timestamps went missing are never revisited. I can't confirm this from the sample alone. Check with `select sum(revenue) from daily_revenue where order_date between '2018-03-01' and '2018-03-31'` and compare the table's last-built time against when `raw_orders` lost the timestamps. The admin reads from the source, which still has the dates.

## 2. Fix

**Data (the actual fix):** Backfill `raw_orders.ordered_at` for ids 66–73 from the source system. Don't infer the timestamps from neighbours. Then fix the ingestion job's parsing for that batch so it doesn't recur.

**Rebuild the history:** The incremental window can't correct March, so after the backfill run:

```
dbt build --select stg_orders+ --full-refresh
```

Use `--full-refresh` on `daily_revenue` specifically, since that is the only table with stale history.

**Optional code hardening:** Make the bad data stop passing silently. In `models/schema.yml`, add `not_null` to `orders.order_date` and in `models/staging/schema.yml` add `not_null` to `ordered_at` on `stg_orders`. Rows with a NULL date should then fail the build instead of disappearing from `daily_revenue` or lowering `first_order`.

## 3. Tests that would have caught this

Add these as singular tests under `tests/`:

**`tests/orders_have_order_date.sql`** (fails if any order has no date):
```sql
select order_id from {{ ref('orders') }} where order_date is null
```

**`tests/daily_revenue_reconciles_to_orders.sql`** (fails if the dashboard drops revenue that `orders` has):
```sql
select
    (select sum(amount) from {{ ref('orders') }} where order_date is not null)
  - (select sum(revenue) from {{ ref('daily_revenue') }}) as gap
where abs(
    (select sum(amount) from {{ ref('orders') }} where order_date is not null)
  - (select sum(revenue) from {{ ref('daily_revenue') }})
) > 0.005
```
The reconciliation test would have failed on the 149.00 gap even while the dashboard showed stale values, because `daily_revenue` only sums dated orders while `orders` holds the full amount.

**`tests/customers_with_orders_have_first_order.sql`** (catches customers 19 and 36):
```sql
select customer_id from {{ ref('customers') }}
where number_of_orders > 0 and first_order is null
```

The `not_null` additions above also cover the first two cases declaratively. If you want, I can apply the schema changes and the three tests and run `dbt build` to confirm they fail against the current data.
