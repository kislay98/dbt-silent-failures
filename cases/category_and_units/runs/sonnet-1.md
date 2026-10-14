claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 47 s | USD 0.098 | 2026-10-14 | project 8ee5734 | prompt d0c0d63b3bb2

## 1. Root cause

**Where:** the `status` column from `raw_orders` (via `models/staging/stg_orders.sql`), masked by the `accepted_values` lists in `models/schema.yml` and `models/staging/schema.yml`.

**What:** around 2018-03-10 the source began emitting a new status, `delivered`, where it previously emitted `completed`.

- In the `orders` output, `completed` appears in Jan, Feb and the first nine March orders, then disappears.
- From then on the shipped-and-finished orders (66-70, 73, 75, 76) are `delivered`, and those 8 orders sum to 134.02.
- Everything downstream filters on `status = 'completed'`, so those orders drop out of `customers.completed_lifetime_value` and the `completed` rows of `orders`. `customers` is consistent with `orders`: 424 + 400 + 143 = 967.
- The admin counts them as completed. The gap is 279 − 143 = 1103 − 967 = **136.00**, the same in both comparisons, so it is all in March.
- No test failed because someone added `delivered` to the `accepted_values` list in both schema files. That list was widened to make the test pass instead of mapping the new value.

**Unexplained 1.98:** the delivered orders sum to 134.02, not 136.00. Every other March order (placed or shipped) is an integer amount. Order 76 is a coupon-only order of 0.02, and the admin may hold it as 2.00.

That would point to a cents/dollars issue on small coupon payments in `raw_payments`, which I can't confirm from what I was given. Run this to check:

```sql
select * from raw_payments where order_id = 76
```

If order 76 shows `amount = 2` and the admin has it as $2.00, that is a second, smaller data issue to take up with ingestion.

## 2. Fix

If the source owner confirms `delivered` means the order is complete, normalise it in staging:

```sql
-- models/staging/stg_orders.sql, in the renamed CTE
case when status = 'delivered' then 'completed' else status end as status
```

Then:

- Drop `'delivered'` from the `accepted_values` lists in both `schema.yml` files.
- Run `dbt build`. March completed should come out at 143.00 + 134.02 = 277.02 in `orders`, and `customers` should reconcile to it.
- Ask the ingestion owner whether `delivered` replaced `completed` or is a distinct state. If it is distinct (say, delivered but not yet closed), instead add a `completed_statuses` var and use it in `customers.sql` and the reporting models.

## 3. Tests that would have caught it

**Stop widening the list.** Keep `accepted_values` on `stg_orders.status` limited to the canonical statuses, so a new upstream value fails the build and gets reviewed.

**Singular test for a status that vanishes.** This fails if a month with orders has no completed orders, and it would have been red once the March data arrived:

```sql
-- tests/assert_completed_orders_every_closed_month.sql
with monthly as (
    select
        date_trunc('month', order_date) as month,
        count(*) filter (where status = 'completed') as completed_orders
    from {{ ref('orders') }}
    group by 1
)
select * from monthly
where completed_orders = 0
  and month < (select date_trunc('month', max(order_date)) from {{ ref('orders') }})
```

**Reconciliation to the admin.** Load the admin's monthly completed totals as a seed and compare them to `orders`. That would have shown 143.00 against 279.00 immediately.
