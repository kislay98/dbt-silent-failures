claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 60 s | USD 0.009 | 2026-10-14 | project ad0b16b | prompt 3b4c114442ec

## 1. Root cause

**The raw orders feed contains duplicate order rows, and nothing in the project removes them.** The duplicates are in `raw_orders`, which the ingestion job loads, so the bug is upstream of every model here. `stg_orders.sql` passes all rows through unchanged, and `stg_payments.sql` does the same for their payments.

The sample data shows the pattern:

- Orders with ids 100-115 are copies of existing orders with the same `user_id` and `ordered_at`. For example, order 41 and order 105 are both customer 99 at `2018-02-14 10:44:12`, both `completed`.
- The standard 99 orders plus 16 copies gives 115 rows, which matches the `orders` count and the 16-order gap against the admin.
- The amount gap is 1913.00 − 1672.00 = 241.00, which is the sum of the 16 copies.
- Customer 51 has 2 copies, which explains 5 orders in `customers` versus 3 in the admin.
- The copies have payments too (payment ids 114-131), so they inflate `amount`, `fees`, and the customer lifetime values.

**Why the tests pass:** `unique` and `not_null` on `order_id` only check that the ids are distinct. The copies have new ids, so the tests have nothing to fail on.

**Why `daily_revenue` matches the admin and the processor:** `daily_revenue` is incremental, and the only rows it rebuilds are the last three days. The copies have February `ordered_at` values and arrived after the February rows were first built. The 3-day window never revisits February, so the table still reflects the pre-duplicate data. Its February numbers (27 orders, 415.00) are right by accident. Month by month, the only differences are the 16 February orders (43 vs 27, and 656.00 vs 415.00). A full refresh today would put the duplicates into `daily_revenue` and make it wrong too.

**Caveat:** I can't see the ingestion job or the full raw tables, so the duplicate pattern is inferred from the sample and the counts. Confirm it with:

```sql
select user_id, ordered_at, count(*) as n, list(id order by id) as ids
from raw_orders
group by 1, 2
having count(*) > 1;
```

I expect 16 extra rows, with the lowest id in each group being an original (≤ 99).

## 2. Fix

**Real fix, outside this repo:** make the ingestion job idempotent. Upsert on the source system's order id and don't insert a new row when the same order is re-sent. Until that's done, dedupe in staging.

**`models/staging/stg_orders.sql`:** keep the original (lowest id) for each `(customer_id, ordered_at)`:

```sql
with source as (

    {#-
    Normally we would select from the table here, but we are using seeds to load
    our data in this project
    #}
    select * from {{ ref('raw_orders') }}

),

deduped as (

    -- the ingestion job re-sends some orders under new ids; keep the original
    select
        *,
        row_number() over (partition by user_id, ordered_at order by id) as rn
    from source

),

renamed as (

    select
        id as order_id,
        user_id as customer_id,
        ordered_at,
        cast(ordered_at as date) as order_date,
        status

    from deduped
    where rn = 1

)

select * from renamed
```

**`models/staging/stg_payments.sql`:** drop payments whose order was removed. The copies' payments aren't in the 1672.00 settlement, so they shouldn't count:

```sql
renamed as (

    select
        id as payment_id,
        order_id,
        payment_method,

        -- `amount` is currently stored in cents, so we convert it to dollars
        amount / 100 as amount

    from source
    where order_id in (select order_id from {{ ref('stg_orders') }})

)
```

**Rebuild:** after the staging fix, run a full refresh so the February rows of `daily_revenue` are rebuilt from clean data:

```
dbt build --full-refresh
```

Don't full-refresh `daily_revenue` before the staging fix goes in.

**Expected results after the fix:**
- `orders`: 99 rows, amount 1672.00
- `daily_revenue`: 99 orders, 1672.00, and it matches `orders` day by day
- `customers`: customer 51 has 3 orders

## 3. Tests that would have caught this

**a) Natural-key uniqueness on staging orders** (dbt_utils). This catches the duplicates at the source:

```yaml
# models/staging/schema.yml, under stg_orders
tests:
  - dbt_utils.unique_combination_of_columns:
      arguments:
        combination_of_columns: [customer_id, ordered_at]
```

This assumes a customer can't place two orders with the same timestamp. If that's possible in your business, use a different combination of columns, such as adding `status`, and confirm with ops.

**b) A reconciliation test between `daily_revenue` and `orders`.** This would have failed on the February days, which is the symptom ops reported:

```sql
-- tests/assert_daily_revenue_matches_orders.sql
select
    coalesce(d.order_date, o.order_date) as order_date,
    d.orders as dashboard_orders,
    o.orders as orders_table_orders,
    d.revenue as dashboard_revenue,
    o.revenue as orders_table_revenue
from {{ ref('daily_revenue') }} d
full outer join (
    select order_date, count(*) as orders, sum(amount) as revenue
    from {{ ref('orders') }}
    group by 1
) o
    on d.order_date = o.order_date
where d.orders is distinct from o.orders
   or round(d.revenue, 2) is distinct from round(o.revenue, 2)
```

Before the fix, this test fails on the February days. After the fix, it passes. It also catches future staleness in the incremental model, since a late-arriving order older than the 3-day window shows up as a mismatch.

**c) Uniqueness on the seed keys that joins fan out on.** `region_revenue` joins `raw_customer_regions` on `customer_id`, and `orders.sql` joins `raw_payment_methods` on `payment_method`. A duplicate in either would silently multiply revenue:

```yaml
# models/schema.yml, under the seeds section
- name: raw_customer_regions
  columns:
    - name: customer_id
      tests:
        - not_null
        - unique
- name: raw_payment_methods
  columns:
    - name: payment_method
      tests:
        - not_null
        - unique
```

## Other things I noticed

- `daily_revenue.sql`'s comment says the 3-day rebuild is for payments landing late. It doesn't cover late-arriving orders older than three days, which is what happened here. Either widen the window or rely on the reconciliation test above.
- `models/schema.yml` documents a `total_order_amount` column on `customers`, which doesn't exist (the model has `customer_lifetime_value`). It's harmless to the tests but misleading.

I haven't changed any files. I can apply the staging fixes, the new tests, and the schema changes if you want me to.
