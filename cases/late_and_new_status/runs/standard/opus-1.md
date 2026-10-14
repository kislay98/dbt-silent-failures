claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 63 s | USD 0.243 | 2026-10-14 | project ad0b16b | prompt 2b2290cfef14

# Diagnosis: 14 orders arrived with a new status, `delivered`

## 1. Root cause

The numbers line up exactly with the 14 `delivered` orders (201.00):

| Check | Value |
|---|---|
| Admin completed since January | 1168.00 |
| `completed` (967.00) + `delivered` (201.00) | 1168.00 |
| Admin completed in March | 279.00 |
| `completed` in March | 143.00 |
| `delivered` in March (279 − 143) | 136.00 |
| `delivered` outside March (201 − 136) | 65.00 |
| February gap (`orders` 480.00 − `daily_revenue` 415.00) | 65.00, 6 orders |

So the 14 `delivered` orders are most likely 6 in February (65.00) and 8 in March (136.00). This is an inference from the totals; the first query below confirms it.

**What is wrong in the data:** `raw_orders` (loaded by the ingestion job, not dbt) now contains `status = 'delivered'` for orders the shop treats as completed. The February ones also arrived, or were rewritten, after their dates had left `daily_revenue`'s 3-day window. This hit three files:

- **`models/customers.sql`** counts only `orders.status = 'completed'` in `completed_amount`. It drops all 14 `delivered` orders, which gives 967.00 instead of 1168.00. The March "completed" figure in `orders` (143.00) has the same problem.
- **`models/daily_revenue.sql`** only rebuilds `order_date >= max(order_date) - 3 days`. The 6 February orders landed after February had already been built, so `daily_revenue` never picked them up. Its February is still 27 orders and 415.00, while `orders` (a full rebuild) shows 33 and 480.00.
- **`models/schema.yml` and `models/staging/schema.yml`** both have `'delivered'` added to the `accepted_values` lists. Without that, the test would have failed when the new status appeared. With it, `dbt build` passes. Nothing checks that `daily_revenue` reconciles with `orders`.

Queries to confirm:

```sql
-- expect: delivered = 6 orders / 65.00 in Feb, 8 / 136.00 in Mar
select date_trunc('month', order_date) as month, status, count(*), sum(amount)
from orders where status in ('completed', 'delivered') group by 1, 2 order by 1, 2;

-- expect: the Feb days containing those 6 orders
select o.order_date, count(*) as orders, sum(o.amount) as revenue, d.orders as dr_orders, d.revenue as dr_revenue
from orders o left join daily_revenue d using (order_date)
group by o.order_date, d.orders, d.revenue
having count(*) is distinct from d.orders or sum(o.amount) is distinct from d.revenue;
```

## 2. Fix

**Map the status once, in staging.** Confirm with the shop team that `delivered` means completed; the admin totals say it does. Then `models/staging/stg_orders.sql`:

```sql
    select
        id as order_id,
        user_id as customer_id,
        ordered_at,
        cast(ordered_at as date) as order_date,
        status as source_status,
        -- the shop started emitting 'delivered' for completed orders in 2018-02
        case when status = 'delivered' then 'completed' else status end as status

    from source
```

`customers.sql` needs no change. The `completed` filter in `customers` and the completed figures in `orders` are now correct.

Update both schema files:

- Remove `'delivered'` from `accepted_values` on `stg_orders.status` and `orders.status`, so the canonical status set stays strict.
- Add a strict test on `source_status` (see section 3).

**Fix `daily_revenue`.**

1. Backfill once: `dbt build -s daily_revenue --full-refresh`. This restores February to 33 orders and 480.00.
2. Stop it recurring. The table is 69 rows, so I recommend dropping the incremental config and materializing it as a table, like `orders`. If it must stay incremental, the 3-day lookback can't handle backfills older than that. Add a scheduled `--full-refresh` (e.g. weekly) and the reconciliation test below.

## 3. Tests that would have caught this

**a) Reconcile `daily_revenue` against `orders`.** This would have failed on the February rows. Add `tests/assert_daily_revenue_matches_orders.sql`:

```sql
with expected as (
    select order_date, count(*) as orders, sum(amount) as revenue
    from {{ ref('orders') }}
    group by order_date
)

select coalesce(e.order_date, d.order_date) as order_date,
       e.orders as expected_orders, d.orders,
       e.revenue as expected_revenue, d.revenue
from expected e
full outer join {{ ref('daily_revenue') }} d using (order_date)
where e.orders is distinct from d.orders
   or abs(coalesce(e.revenue, 0) - coalesce(d.revenue, 0)) > 0.005
```

**b) Make a new source status a hard failure.** Put the strict list on the raw value in `models/staging/schema.yml`:

```yaml
      - name: source_status
        tests:
          - accepted_values:
              arguments:
                values: ['placed', 'shipped', 'completed', 'return_pending', 'returned', 'delivered']
      - name: status
        tests:
          - accepted_values:
              arguments:
                values: ['placed', 'shipped', 'completed', 'return_pending', 'returned']
```

Now a new upstream status fails `source_status` until someone adds it there and decides how it maps in `stg_orders`. In this incident, adding the value to the list alone made the build pass silently. Together, the two lists force a decision on what the new status means for completed revenue.
