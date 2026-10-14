claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 20 s | USD 0.063 | 2026-10-14 | project 7d5b525+dirty

## 1. Root cause

The problem is in `seeds/raw_payments.csv`. It isn't in any model's SQL. 13 of its 113 payments have an `order_id` that doesn't exist in `raw_orders.id`, and they total exactly 140.00. That is the whole gap: 1672.00 − 1532.00 = 140.00.

Both finance-facing models silently drop those rows:
- **`orders.sql`** starts from `stg_orders` and left-joins the aggregated payments. A payment with no matching order is never picked up, so order 25 only shows 42.00. Its third payment (16.00, which accounts for the 58 − 42 gap) is almost certainly one of the 13 orphans, with a wrong or typo'd `order_id` in the seed.
- **`region_revenue.sql`** has `join orders on payments.order_id = orders.order_id`, an inner join, so it drops the same payments.
- **`customers.sql`** has the same leak. Orphans land in a `customer_id = null` group in `customer_payments`, which the final join never reaches. `daily_revenue` is built on `orders`, so it is short too.

Every test passed because none of them checks that `stg_payments.order_id` points at a real order. The `relationships` test exists only on `orders.customer_id`, and `orders.amount` is only checked for `not_null`, which passes because the left join gives no nulls for orders that have payments. Nothing compares totals between `stg_payments` and the marts.

I can't see the seed rows, so I can't say whether each orphan is a mistyped `order_id` or a payment for an order missing from `raw_orders.csv`. The query below tells you which.

## 2. Fix

Find the orphans:

```sql
select p.id, p.order_id, p.payment_method, p.amount
from raw_payments p
left join raw_orders o on o.id = p.order_id
where o.id is null
order by p.order_id;
```

Then correct the data at the source:
- If an orphan is a bad key (likely for the 16.00 payment belonging to order 25), fix its `order_id` in `seeds/raw_payments.csv`.
- If the order is missing, add it to `seeds/raw_orders.csv`.
- If the payment really is junk, remove it, and have finance agree to a settlement figure that excludes it.

Then rebuild with `dbt build --full-refresh`. The full refresh matters because `daily_revenue` only rebuilds the last 3 days, so older corrected days would stay stale.

To stop this recurring silently, make `region_revenue` and `customers` start from `orders` rather than `payments`, or fail loudly on orphans using the tests below.

## 3. Tests

Referential integrity in `models/staging/schema.yml`:

```yaml
  - name: stg_payments
    columns:
      - name: order_id
        tests:
          - not_null
          - relationships:
              arguments:
                to: ref('stg_orders')
                field: order_id
```

A reconciliation test in `tests/assert_orders_amount_matches_payments.sql`. It returns a row, and so fails, if the totals differ:

```sql
select p.total as payments_total, o.total as orders_total
from (select sum(amount) as total from {{ ref('stg_payments') }}) p
cross join (select sum(amount) as total from {{ ref('orders') }}) o
where abs(p.total - o.total) > 0.005
```

A similar test with `region_revenue.revenue` against `stg_payments` would catch the regional report drifting. The `relationships` test would have failed on the 13 orphan rows at the staging layer, before the numbers reached finance.
