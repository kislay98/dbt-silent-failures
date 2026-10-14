claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 43 s | USD 0.214 | 2026-10-14 | project ad0b16b | prompt 3b4c114442ec

# Diagnosis: duplicate orders re-ingested with new IDs

## 1. Root cause

**The data is wrong, not the models.** The ingestion job loaded 16 orders into `raw_orders` a second time, under new surrogate IDs **100–115**. It also loaded their payments into `raw_payments` again, as 18 rows with new payment IDs. Every dbt test checks `id`, and the new IDs are unique, so all 35 tests pass.

The output you sent supports this:

| Observation | Value |
|---|---|
| `raw_orders` rows vs admin | 115 vs 99, so 16 extra |
| `orders` vs settlement | 1913.00 vs 1672.00, so 241.00 extra |
| February in `orders` vs `daily_revenue` | 43 / 656.00 vs 27 / 415.00, so +16 orders and +241.00 |
| Jan, Mar and Apr | Identical in both tables |

All of the excess is in February, and it accounts for the entire gap in both the order count and the amount.

You can see one duplicate pair directly in the `stg_orders` sample:

```
order 41   customer 99   2018-02-14 10:44:12   completed
order 105  customer 99   2018-02-14 10:44:12   completed
```

The same customer, timestamp and status appear under two IDs. Customer 51 has orders 25 and 114 in the sample. Five orders against three in the admin means two of the 16 duplicates are theirs.

**Why `daily_revenue` disagrees.** `daily_revenue.sql` is incremental and only rebuilds `order_date >= max(order_date) - 3 days`, which is early April. The duplicates are dated in February, so they arrived after those days had already been built and were never picked up. `daily_revenue` is therefore correct only by accident. A `--full-refresh` would make it show 115 orders as well. The 3-day lookback also means any late data older than 3 days will be missed silently in the future.

The duplicates also inflate `customers` (`number_of_orders` and lifetime value) and `region_revenue`, whose regions sum to 115 orders and 1913.00.

To confirm the full set before changing anything, run:

```sql
-- expect 16 rows, dup_id 100–115
select d.id as dup_id, o.id as original_id, d.user_id, d.ordered_at, d.status
from raw_orders d
join raw_orders o
  on d.user_id = o.user_id and d.ordered_at = o.ordered_at and d.id > o.id
order by dup_id;

-- expect 18 rows totalling 24100 cents
select count(*), sum(amount) from raw_payments where order_id between 100 and 115;
```

## 2. Fix

**Fix the data at the source.** This is the main fix. Once the queries above confirm the set, delete the duplicates and get the ingestion job fixed. It is assigning new IDs on reload instead of upserting on the source key.

```sql
delete from raw_payments where order_id in (
    select d.id from raw_orders d join raw_orders o
      on d.user_id = o.user_id and d.ordered_at = o.ordered_at and d.id > o.id);
delete from raw_orders where id in (
    select d.id from raw_orders d join raw_orders o
      on d.user_id = o.user_id and d.ordered_at = o.ordered_at and d.id > o.id);
```

Then rebuild everything, including the incremental model, so nothing stale remains:

```
dbt build --full-refresh
```

The result should be 99 orders, `sum(amount) = 1672.00`, three orders for customer 51, and `daily_revenue` matching `orders` on every day.

**Optional staging guard.** If you can't wait for the ingestion fix, you can deduplicate in staging. In `models/staging/stg_orders.sql`:

```sql
deduplicated as (

    -- the ingestion job has re-sent orders under new ids; keep the first id per order
    select *
    from renamed
    qualify row_number() over (partition by customer_id, ordered_at order by order_id) = 1

)

select * from deduplicated
```

In `stg_payments.sql`, add `where order_id in (select order_id from {{ ref('stg_orders') }})`.

I'd prefer the test below over this guard. Silently deduplicating hides the ingestion bug, while a failing build surfaces it.

## 3. Tests that would have caught it

**Duplicate orders under a new ID.** Add `tests/assert_no_duplicate_orders.sql`:

```sql
-- the same order re-ingested under a new id: same customer, same timestamp
select customer_id, ordered_at, count(*) as copies
from {{ ref('stg_orders') }}
group by customer_id, ordered_at
having count(*) > 1
```

This would have failed with 16 rows. If you use `dbt_utils`, `unique_combination_of_columns` on `[customer_id, ordered_at]` in `staging/schema.yml` does the same job.

**Incremental drift.** Add `tests/assert_daily_revenue_matches_orders.sql`:

```sql
-- the incremental model must agree with its source on every day
select coalesce(o.order_date, d.order_date) as order_date, o.orders, d.orders as dr_orders
from (select order_date, count(*) as orders, sum(amount) as revenue
      from {{ ref('orders') }} group by 1) o
full outer join {{ ref('daily_revenue') }} d on o.order_date = d.order_date
where o.orders is distinct from d.orders or o.revenue is distinct from d.revenue
```

This would have flagged the February days the first night the duplicates landed.

## Smaller issues outside this bug

- **`raw_customer_regions` has no `unique` test on `customer_id`.** It has an `updated_at` column, so a second row for a customer is plausible. That would fan out `region_revenue`. Totals currently reconcile with `orders`, so it isn't happening yet.
- **Column name mismatch in `models/schema.yml`.** It documents `total_order_amount`, but the column in `customers` is `customer_lifetime_value`.
