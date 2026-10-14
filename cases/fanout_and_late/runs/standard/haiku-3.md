claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 45 s | USD 0.008 | 2026-10-14 | project ad0b16b | prompt 6c2f9fd42779

## 1. Root cause

**`jaffle_shop/models/daily_revenue.sql`** is the cause of the February mismatch. It is an incremental model that only reprocesses the last three days:

```sql
where order_date >= (select max(order_date) - interval 3 day from {{ this }})
```

The table already holds April rows (the sample has `2018-04-03`), so the cutoff is about 2018-03-31. Last night's backfill added six completed February orders worth 65.00. Those rows have `order_date` values in February, which fall outside the window, so `daily_revenue` never recomputes February. `orders` is a full `table` rebuild, so it picks up the backfill. That is why February now shows 33 orders / 480.00 in `orders` and 27 / 415.00 in `daily_revenue`. The 6 orders and 65.00 difference matches the backfill exactly.

The two tables matched before only because nothing had changed in earlier months. The comment in the model ("payments can land a day or two after the order") describes a different problem. The filter is on `order_date`, not payment date, so it cannot catch late-arriving rows for older dates.

**Nothing in the test suite could catch this.** `daily_revenue` only has `not_null` and `unique` tests, and no test compares it with `orders`.

**Secondary data problem: `jaffle_shop/models/region_revenue.sql` joins a seed with duplicate customers.** `raw_customer_regions` has 106 rows for 100 customers. `region_revenue` sums `payments.amount` after joining to that seed, so the duplicates inflate revenue. The region revenues sum to 1838.0, but total order revenue is 1737.0, a difference of 101. Customer counts are unaffected (the regions sum to 65 distinct customers with orders), which suggests the extra rows repeat the same region for a customer, not conflicting regions. Relatedly, `raw_customer_regions` has an `updated_at` column, which suggests the feed is a history table and should be reduced to one row per customer.

**Also noticed:** the nightly log says "Found 32 data tests" but only 28 ran. The 4 missing tests are the seed tests in `schema.yml` (`raw_customer_regions` and `raw_payment_methods`). The log doesn't show why they were skipped. Check whether seeds or their tests are excluded in the nightly selector.

I'm working from the pasted output and haven't queried the warehouse, so the diagnosis above is inferred from those results.

## 2. Fix

**Immediate data repair** (rebuilds the table from scratch and picks up February):

```sh
dbt build --select daily_revenue --full-refresh
```

**Code fix:** `daily_revenue` is a 105-row aggregate over a table that is already fully rebuilt, so incremental buys nothing and is the source of the bug. Make it a full table:

```sql
-- jaffle_shop/models/daily_revenue.sql
with orders as (

    select * from {{ ref('orders') }}

),

final as (

    select
        order_date,
        count(*) as orders,
        sum(amount) as revenue

    from orders

    group by order_date

)

select * from final
```

Set `materialized: table` for this model in `dbt_project.yml` or in the model config. If it ever needs to be incremental, the window must cover backfilled history, for example by doing a periodic full refresh.

**Region fix:** de-duplicate to the latest row per customer in a staging model. This assumes the latest `updated_at` wins, which is a business decision to confirm.

```sql
-- jaffle_shop/models/staging/stg_customer_regions.sql
with source as (

    select * from {{ ref('raw_customer_regions') }}

),

ranked as (

    select
        customer_id,
        region,
        row_number() over (partition by customer_id order by updated_at desc) as rn

    from source

)

select customer_id, region from ranked where rn = 1
```

Then change `region_revenue.sql` to `{{ ref('stg_customer_regions') }}` instead of `ref('raw_customer_regions')`. After the change, region revenues should sum to 1737.0.

## 3. A test that would have caught this

**Reconciliation test:** a singular test in `jaffle_shop/tests/assert_daily_revenue_matches_orders.sql`. It fails on the nightly build for any date where `daily_revenue` disagrees with `orders`. It would have failed on the first run after the backfill arrived.

```sql
-- Fails for any order_date where daily_revenue disagrees with orders
with from_orders as (

    select
        order_date,
        count(*) as orders,
        sum(amount) as revenue

    from {{ ref('orders') }}

    group by order_date

)

select
    coalesce(o.order_date, d.order_date) as order_date,
    o.orders as orders_expected,
    d.orders as orders_actual,
    o.revenue as revenue_expected,
    d.revenue as revenue_actual

from from_orders o

full outer join {{ ref('daily_revenue') }} d
    on o.order_date = d.order_date

where o.order_date is null
    or d.order_date is null
    or d.orders <> o.orders
    or abs(d.revenue - o.revenue) > 0.005
```

**Duplicate-region test:** the seed's description says "One row per customer", but the only test on `customer_id` is `not_null`. Add `unique` to `raw_customer_regions.customer_id` in `models/schema.yml`. That would have flagged the 106-row seed.
