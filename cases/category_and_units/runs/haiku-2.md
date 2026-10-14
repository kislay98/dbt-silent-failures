claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 89 s | USD 0.011 | 2026-10-14 | project 8ee5734 | prompt d0c0d63b3bb2

## Bottom line

The models are consistent with each other, so this isn't a join or status-filter bug in `customers.sql` or `orders.sql`. The gap is **136.00 of March completed revenue** that the admin has and the warehouse doesn't. Neither the pasted log nor the sample shows which of three causes produced it, so I can't name a single root cause yet. The queries below settle it.

## Reconciliation

- `customers.completed_lifetime_value` sums to 967.00, which equals completed orders in `orders` for Jan + Feb + Mar (424 + 400 + 143 = 967). The two models agree.
- Admin minus warehouse is 279 − 143 = 136 for March, and 1103 − 967 = 136 since January. The entire gap is in March.
- A fan-out through `raw_payment_methods` would inflate amounts, not shrink them, and the completed totals match, so it isn't affecting completed orders.

Candidate causes, most likely first:

1. **Stale tables.** `orders` and `customers` are materialized tables built at 13:54, while `stg_*` are live views over `raw_*`. If March rows landed in raw after the build, the tables lag.
2. **Late payments.** An order can have one payment in `raw_payments` and a second that hasn't arrived. `orders.amount` is still non-null, so `not_null` passes. `raw_payments` has no timestamp, so this can't be seen from the warehouse. The comment in `daily_revenue.sql` says payments land late, which points the same way.
3. **Different rules in admin.** For example, admin may count a status or date rule the warehouse doesn't. Timezone alone is unlikely to explain the since-January gap, because the data starts in January 2018.

Run these to decide:

```sql
-- 1. Live raw vs built table. If this returns 279, `orders` is stale: rebuild it.
select sum(p.amount) / 100.0 as amount
from raw_orders o
join raw_payments p on p.order_id = o.id
where o.status = 'completed'
  and o.ordered_at >= '2018-03-01' and o.ordered_at < '2018-04-01';

-- 2. Identify the 136. Export the admin's March completed order IDs, then diff:
select o.id, o.ordered_at, o.status
from raw_orders o
where o.status = 'completed'
  and o.ordered_at >= '2018-03-01' and o.ordered_at < '2018-04-01'
  and o.id not in (/* admin order IDs */);
-- Then look up those orders in raw_payments: missing rows point to ingestion,
-- a short payment total points to late payments.
```

## 1. Root cause and fix

**Root cause:** not determinable from the pasted material. The likely location is data, not `customers.sql` or `orders.sql`.

**Fix, by what query 1 and 2 show:**

- **Stale build:** run `dbt build` after the ingestion job finishes, or make the build depend on ingestion completing. Rebuilding `orders+` and `customers+` alone fixes the numbers.
- **Late payments:** rebuild after payments land. Because `orders` and `customers` are full rebuilds, the next nightly run picks them up. If finance needs same-day numbers, add a lookback rule or check payment counts per order before publishing.
- **Missing raw rows:** fix at ingestion and re-run the reconciliation below.

**Latent defects I found in the files that the tests miss.** Check these even though they don't explain the 136:

- **Seed tests didn't run.** The log reports 32 data tests found and 28 run. The 4 missing are the not_null tests on `raw_customer_regions` and `raw_payment_methods`, and no seed was built. Include seeds in the build, or run `dbt test --select raw_customer_regions raw_payment_methods`.
- **`raw_payment_methods` has a `processor` column but no unique test on `payment_method`.** If there are several rows per method, `orders.sql` fans out payments. Decide which processor row is authoritative and dedupe in the model. Add `unique` on `payment_method`.
- **`raw_customer_regions` has `updated_at`, which implies several rows per customer.** `region_revenue.sql` joins it directly, so `sum(payments.amount)` double-counts. Keep only the latest row:

```sql
regions as (
    select customer_id, region
    from (
        select *, row_number() over (partition by customer_id order by updated_at desc) as rn
        from {{ ref('raw_customer_regions') }}
    )
    where rn = 1
),
```

- **`daily_revenue` has no status filter**, so its `revenue` includes placed, returned, and every other status. If finance reads "revenue" from there, that alone can look wrong.
- **Doc drift:** `schema.yml` documents `total_order_amount` on `customers`, which the model doesn't output.

## 2. Test

A reconciliation test catches the fan-out and dropped-payment class of bugs:

```sql
-- tests/assert_order_amounts_reconcile_to_payments.sql
-- every payment should land on exactly one order, so totals must match
with built as (
    select sum(amount) as amt from {{ ref('orders') }}
),
source as (
    select sum(amount) as amt from {{ ref('stg_payments') }}
)
select built.amt as orders_total, source.amt as payments_total
from built
cross join source
where abs(built.amt - source.amt) > 0.005
```

Be clear about its limit: it would not have caught this incident by itself. Late or missing payments are internally consistent in the warehouse, so only a comparison against an external figure sees them. Add a daily check that compares the admin's monthly completed total, exported into a source table, with `customers.completed_lifetime_value` for the same month. That check would have failed on the first day March was wrong.

If you point me at the DuckDB file or give me the admin export, I can run the queries and confirm which cause it is.
