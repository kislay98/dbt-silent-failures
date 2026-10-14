claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 21 s | USD 0.064 | 2026-10-14 | project 7d5b525+dirty

## 1. Root cause

The loss is in `seeds/raw_payments.csv` (surfaced through `stg_payments`), not in the SQL logic. 13 payment rows totalling 140.00 have an `order_id` that matches no `id` in `raw_orders`. This is the output of your orphan query: 13 rows, 140.00. They may be `NULL`, mistyped, or point at orders that were never loaded.

The numbers reconcile exactly: 1672.00 − 140.00 = 1532.00.

Two models then drop those rows without any error:
- `orders.sql` starts from `stg_orders` and left-joins `order_payments`, so a payment with no parent order never appears in any order's `amount`.
- `region_revenue.sql` starts from payments but inner-joins `stg_orders`, so the same rows are discarded. That is why it also shows 1532.00.
- `daily_revenue` is built from `orders`, so it inherits the gap.

Order 25 is most likely one of these cases. One of its three payments (16.00, given 58 − 42) was probably keyed to a wrong or missing `order_id`, so only 42.00 was attributed to it. I can't confirm this without the seed data. Run this to see the 13 rows:

```sql
select p.*
from stg_payments p
left join stg_orders o using (order_id)
where o.order_id is null
order by payment_id;
```

Every test passed because none of them checks referential integrity from payments to orders. The `not_null` tests on `orders.amount` pass because every order still has at least one payment. Nothing compares totals across models.

## 2. Fix

1. **Fix the data.** Correct the `order_id` on those 13 rows in `raw_payments.csv`, or add the missing orders to `raw_orders` if the orders are what's absent. Don't patch this in the models, because the mapping is a business decision. Check that the 16.00 row belongs to order 25.
2. **Make the models stop hiding it.** Once the data is fixed, leave the joins as they are. If orphans can legitimately occur in future, surface them rather than drop them. For example, have `region_revenue` left-join orders and regions and report an `unknown` bucket, so the totals always tie to the processor.
3. Run `dbt build` again. `daily_revenue` is incremental and only rebuilds the last 3 days, so run `dbt build --full-refresh -s daily_revenue` to correct history.

## 3. Tests that would have caught it

Add to `models/staging/schema.yml` under `stg_payments`:

```yaml
      - name: order_id
        tests:
          - not_null
          - relationships:
              arguments:
                to: ref('stg_orders')
                field: order_id
```

Add a singular test, `tests/assert_revenue_reconciles_to_payments.sql`, which fails if any downstream total drifts from the processor total:

```sql
with p as (select sum(amount) as t from {{ ref('stg_payments') }}),
     o as (select sum(amount) as t from {{ ref('orders') }}),
     r as (select sum(revenue) as t from {{ ref('region_revenue') }})
select p.t as payments, o.t as orders, r.t as regions
from p, o, r
where abs(p.t - o.t) > 0.005
   or abs(p.t - r.t) > 0.005
```

I also suggest adding `unique` on `raw_customer_regions.customer_id`. The seed is described as one row per customer but isn't tested for it, and duplicates would silently inflate the join in `region_revenue`.
