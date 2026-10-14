claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 64 s | USD 0.009 | 2026-10-14 | project f769fdf+wk6ext | prompt c5114118dec9

## 1. Root cause

There are two separate bugs. Both are in the models, and the data itself is consistent.

**A. `customers.completed_lifetime_value` ignores `delivered` orders.** The file is `jaffle_shop/models/customers.sql`, in `customer_payments`:

```sql
sum(case when orders.status = 'completed' then amount else 0 end) as completed_amount
```

`delivered` was added as a valid status (it is in both `accepted_values` lists), but this filter was never updated. The admin's 1168.00 is exactly `completed + delivered`:

| Month | completed | delivered | completed + delivered | Admin |
|---|---|---|---|---|
| Jan | 424 | 0 | 424 | 424 |
| Feb | 400 | 65 | 465 | 465 |
| Mar | 143 | 136 | 279 | 279 |

Total: 424 + 465 + 279 = 1168. The `orders` table is correct for what it contains. The problem is the definition of "completed" in `customers.sql`. The `accepted_values` test passes because it only checks that a status is in the list, not how each status is used.

**B. `daily_revenue` is missing six February orders.** The file is `jaffle_shop/models/daily_revenue.sql`. It is incremental and only rebuilds rows where:

```sql
where order_date >= (select max(order_date) - interval 3 day from {{ this }})
```

The window is based on the latest date already in the table. Order IDs 100–105 are February orders that are not in `daily_revenue`. Their `order_date` is older than the window, so no run will ever rebuild them. Orders 98 and 99 are dated April, so the window starts around 2018-04-06, and February is never touched again. The missing rows add up exactly:

- Orders 100–105 are all `delivered`, with amounts 12 + 9 + 15 + 6 + 20 + 3 = 65.00 and six orders.
- 480.00 − 415.00 = 65.00 and 33 − 27 = 6.

The likely cause is that these orders reached `raw_orders` after `daily_revenue` had already advanced past February. The comment in the model says the lookback exists for late payments, but the filter is on `order_date`, so it cannot handle late-arriving orders at all. The `orders` table has no such problem, since it is a full rebuild.

I'm inferring the load timing from the data, since I can't see when the ingestion job loaded these rows. The arithmetic above is exact, though.

## 2. Fix

**customers.sql:** count both statuses as completed, and make the rule explicit:

```sql
sum(case when orders.status in ('completed', 'delivered') then amount else 0 end) as completed_amount
```

Update the `completed_lifetime_value` description in `models/schema.yml` to say "status completed or delivered". The admin's numbers match this definition exactly, but please have finance confirm that `delivered` counts as completed before merging. If it does, this is the definition to keep. Ideally it would live in one place, such as a `is_completed` column in `stg_orders` or a macro, so future models don't repeat the literal.

**daily_revenue.sql:** this table is a `GROUP BY` over `orders` and is small, so the incremental logic isn't worth keeping. Make it a plain table:

```sql
{{ config(materialized='table') }}
```

Then remove the `is_incremental()` block, which also removes the misleading comment.

To repair the table now, run `dbt run --select daily_revenue --full-refresh` (or just `dbt run --select daily_revenue` after the config change). That restores the six February orders.

## 3. Test that would have caught this

**For B, a reconciliation singular test.** This would have failed on the February rows. Save it as `tests/assert_daily_revenue_matches_orders.sql`:

```sql
with from_orders as (

    select
        order_date,
        count(*) as orders,
        sum(amount) as revenue

    from {{ ref('orders') }}
    group by order_date

),

from_daily as (

    select order_date, orders, revenue
    from {{ ref('daily_revenue') }}

)

select
    order_date,
    o.orders as orders_in_orders,
    d.orders as orders_in_daily_revenue,
    o.revenue as revenue_in_orders,
    d.revenue as revenue_in_daily_revenue

from from_orders o
full outer join from_daily d using (order_date)

where o.orders is distinct from d.orders
   or round(o.revenue, 2) is distinct from round(d.revenue, 2)
```

**For A, a unit test** that encodes the business rule. Add it to `models/schema.yml`:

```yaml
unit_tests:
  - name: completed_lifetime_value_includes_delivered
    model: customers
    given:
      - input: ref('stg_customers')
        rows:
          - {customer_id: 1, first_name: a, last_name: b}
      - input: ref('stg_orders')
        rows:
          - {order_id: 1, customer_id: 1, ordered_at: '2018-03-01', order_date: '2018-03-01', status: delivered}
      - input: ref('stg_payments')
        rows:
          - {payment_id: 1, order_id: 1, payment_method: credit_card, amount: 10}
    expect:
      rows:
        - {customer_id: 1, completed_lifetime_value: 10}
```

This test will fail until the `customers.sql` fix is in. It also records the decision that `delivered` counts as completed.

## Other things I noticed (not in the reported numbers)

- **`region_revenue` may double-count.** `raw_customer_regions` has an `updated_at` column, which suggests more than one row per customer. The join on `customer_id` would then fan out revenue. The totals may look right only because the current feed has one row per customer. Add a `unique` test on `customer_id` in the seed, or dedupe to the latest row.
- **`orders` could fan out the same way.** `raw_payment_methods` has a `processor` column, so a payment method with two processors would duplicate payments in the `left join`. The seed's schema only tests `not_null`. Add a `unique` test on `payment_method`.
- **`schema.yml` documents a column that doesn't exist.** `customers` lists `total_order_amount`, which the model doesn't produce.

I did not run these changes. Everything above is based on the logs, schema, and sample outputs you provided.
