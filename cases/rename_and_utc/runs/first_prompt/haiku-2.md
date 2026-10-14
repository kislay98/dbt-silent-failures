claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 35 s | USD 0.005 | 2026-10-14 | project f769fdf+wk6ext | prompt c5676332c543

## 1. Root cause

**`jaffle_shop/models/staging/stg_orders.sql`** reads only `ordered_at`, but the ingestion job now also fills `order_ts` in `raw_orders`. From order 57 (2018-03-01) onward, `ordered_at` is NULL and `order_ts` holds the value. Those 43 orders get `order_date = NULL`.

- **`orders`**: the 43 NULL-date orders (761.00) fall into a NULL month. The March and April rows never appear. The sample output confirms this.
- **`daily_revenue`**: it still shows March and April because it is incremental. Those rows were written before the bad load and are never rewritten. Its `where order_date >= ...` filter also drops NULL dates, so the 43 orders never enter it. Its `not_null` and `unique` tests pass because the NULL rows never reach the table.
- **`customers`**: customers 9, 12 and 93 show NULL `first_order` because their only orders have NULL dates. Customer 22's `most_recent_order` is 2018-02-17, although order 58 (2018-03-01) is theirs. `number_of_orders` is still correct.
- **The tests pass** because nothing tests `order_date` for NULLs. `orders.order_date` and `stg_orders.ordered_at` have no `not_null` test, and `daily_revenue`'s test is blind to the problem.

**Secondary issue, the timezone mismatch.** `order_date` is the UTC date (`cast(ordered_at as date)`, documented as "Date (UTC)"). Ops reconciles against the admin screen in shop-local time. Order 73 is `2018-03-15 19:45:20` UTC, which is `2018-03-16 01:15:20` on the admin screen. That is a +05:30 offset, so the shop is probably on `Asia/Kolkata`. Confirm the zone name with ops. Any order placed after 18:30 UTC lands on the wrong day in the report.

## 2. Fix

**a) Stop dropping the timestamp.** In `jaffle_shop/models/staging/stg_orders.sql`:

```sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,
        coalesce(ordered_at, order_ts) as ordered_at,
        cast(coalesce(ordered_at, order_ts) as date) as order_date,
        status

    from source

)
```

Before merging, check in the warehouse that `ordered_at = order_ts` for every row where both are non-NULL. If they differ anywhere, decide which column is authoritative. Also ask the ingestion owner why `ordered_at` stopped being populated, since that is what needs fixing upstream.

**b) Make `order_date` the shop-local date (my recommendation, since ops reconciles this way).** Replace the `order_date` line above with:

```sql
cast(timezone('Asia/Kolkata', timezone('UTC', coalesce(ordered_at, order_ts))) as date) as order_date,
```

Then update the `Date (UTC)` descriptions in `jaffle_shop/models/schema.yml` for `orders.order_date` and `customers.first_order`/`most_recent_order`. If you would rather keep UTC, do not change this line, and tell ops to reconcile against UTC dates instead.

**c) Rebuild `daily_revenue` from scratch.** The incremental window will not rewrite March and April, so a normal run will not fix it:

```
dbt build --select stg_orders+ --full-refresh
```

I have not run this. These fixes are derived from the log and the sample outputs.

## 3. Tests that would have caught it

**a) Schema tests.** Add to `jaffle_shop/models/staging/schema.yml`:

```yaml
  - name: stg_orders
    columns:
      - name: ordered_at
        tests:
          - not_null
      - name: order_date
        tests:
          - not_null
```

This fails on the 43 NULL rows at the staging layer, before anything downstream is built.

**b) A reconciliation test.** Create `jaffle_shop/tests/assert_daily_revenue_matches_orders.sql`:

```sql
with from_orders as (

    select
        order_date,
        count(*) as orders,
        sum(amount) as revenue

    from {{ ref('orders') }}
    where order_date is not null
    group by order_date

),

from_daily as (

    select order_date, orders, revenue
    from {{ ref('daily_revenue') }}

)

select coalesce(o.order_date, d.order_date) as order_date
from from_orders o
full outer join from_daily d using (order_date)
where o.orders is distinct from d.orders
   or round(o.revenue, 2) is distinct from round(d.revenue, 2)
```

This would have failed on March and April. It would also catch the incremental model drifting from `orders` in future, which the current `not_null` and `unique` tests on `daily_revenue` cannot detect.
