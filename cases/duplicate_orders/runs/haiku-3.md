claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 43 s | USD 0.006 | 2026-10-14 | project bef6b69

## 1. Root cause

**The duplicate orders come from the raw data, not from the model SQL.** `raw_orders` (loaded by ingestion) contains 16 extra rows, most likely ids 100–115. Each one repeats an existing order's customer, date, and amount under a new `id`:

| Duplicate id | Copy of | customer | order_date | amount |
|---|---|---|---|---|
| 101 | 37 | 1 | 2018-02-10 | 23.0 |
| 102 | 38 | 51 | 2018-02-10 | 15.0 |
| 114 | 50 | 51 | 2018-02-23 | 26.0 |

This accounts for every number in the report:

- **Order count:** 115 in `orders` vs 99 in the admin. 115 − 99 = 16 extra rows.
- **Revenue:** 1913.00 − 1672.00 = 241.00, the sum of the duplicate amounts.
- **Customer 51:** orders 25, 38, 50 are real. 102 and 114 are copies, giving 5 instead of 3.

Ids 100–115 are my inference from the counts and the sample. Confirm with the query at the end of this section.

Each duplicate also has its own payment rows in `raw_payments`, which is why `amount` is non-null and the `not_null` tests pass. The processor never settled those payments, so settlement (1672.00) matches the originals only. That suggests the ingestion job re-loaded a batch of orders and payments with fresh surrogate ids. I can't see the ingestion code, so I can't confirm why.

The models pass the duplicates through unchanged:

- `models/staging/stg_orders.sql` selects every row from `raw_orders` with no dedup. It is the first place the duplicates enter the DAG.
- `models/orders.sql` inherits them through `stg_orders` and adds the duplicate payment totals.
- `models/customers.sql` inherits them too, which is why customer 51 shows five orders.

The `unique` test on `order_id` passes because the duplicates have distinct ids. The tests check key integrity, not whether the same order was loaded twice.

**Why `daily_revenue` looks right and `orders` looks wrong.** `models/daily_revenue.sql` is the one that matches the admin (99 orders, 1672.00). Its incremental filter only rebuilds `order_date >= max(order_date) - 3 days` from the table's own max date. The data runs to spring 2018, so the rebuild window covers only the latest dates. February rows built before the duplicates arrived are never refreshed. The comment says the window handles late-arriving payments, but it doesn't handle late-arriving history.

Check for the duplicates with:

```sql
select customer_id, ordered_at, count(*) from raw_orders group by 1, 2 having count(*) > 1;
```

Also check whether the copies share `ordered_at`. If they do, that is the natural key to dedupe on.

## 2. Fix

**Data (needs your confirmation before running):**

1. Confirm with ops and the processor which `raw_orders` rows are duplicates (ids 100–115 is my hypothesis). Remove those order rows and their matching `raw_payments` rows from the raw layer. This is destructive, so I'd do it only after sign-off.
2. Fix the ingestion job so re-loads are idempotent: upsert on the source order id, or reject rows whose natural key already exists.

**Model:**

3. Rebuild `daily_revenue` from scratch so the stale February rows are recomputed: `dbt build --select daily_revenue --full-refresh`.
4. Optionally, change `daily_revenue` to `materialized='table'`. The dataset is small, and the incremental window cannot correct history. If you keep it incremental, drop the misleading comment or base the window on ingestion time instead of `order_date`.

Once the raw duplicates are gone, `orders`, `customers`, and `daily_revenue` should all show 99 orders and 1672.00 in total.

## 3. Tests that would have caught this

**a) Reconcile the daily table against its source.** This would have failed in February:

```sql
-- tests/assert_daily_revenue_matches_orders.sql
with from_orders as (
    select order_date, count(*) as orders, sum(amount) as revenue
    from {{ ref('orders') }}
    group by 1
)
select
    coalesce(o.order_date, d.order_date) as order_date,
    o.orders as orders_from_orders,
    d.orders as orders_in_daily,
    o.revenue as revenue_from_orders,
    d.revenue as revenue_in_daily
from from_orders o
full outer join {{ ref('daily_revenue') }} d
    on o.order_date = d.order_date
where o.orders is distinct from d.orders
   or round(o.revenue, 2) is distinct from round(d.revenue, 2)
```

**b) Natural-key uniqueness on staging orders.** Add to `models/staging/schema.yml`, assuming copies share `ordered_at` (verify with the query above) and that `dbt_utils` is installed. If it isn't, use a singular test with the same `group by ... having count(*) > 1` logic:

```yaml
- name: stg_orders
  tests:
    - dbt_utils.unique_combination_of_columns:
        arguments:
          combination_of_columns: [customer_id, ordered_at]
```

**c) Reconciliation against the processor settlement.** A singular test that compares `sum(orders.amount)` for a period against the settlement total for that period would have caught the 241.00 gap. It needs the settlement data loaded as a source.

## Other things I noticed (not verified)

- `models/region_revenue.sql` joins `raw_customer_regions`, which has an `updated_at` column. If a customer can have more than one region row over time, that join fans out and inflates `revenue`. The `unique` test on `region` won't catch it. Check `count(*)` vs `count(distinct customer_id)` in that seed.
- `models/schema.yml` documents `total_order_amount` on `customers`, but the model outputs `customer_lifetime_value`.

I haven't changed any files or run anything. The above is based only on the logs, schema, and samples provided.
