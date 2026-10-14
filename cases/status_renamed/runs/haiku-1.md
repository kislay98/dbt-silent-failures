claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 20 s | USD 0.004 | 2026-10-22

## 1. Root cause

**`jaffle_shop/models/staging/stg_orders.sql`** reads only `status` from `raw_orders`. The raw table also has an `order_status` column, which nothing references. For a subset of orders the source populates `order_status` and leaves `status` NULL, so those orders reach `orders` and `customers` with a NULL status.

The output sample shows this:

- Monthly breakdown: 2018-01 and 2018-02 have 29 and 27 orders with a status. 2018-03 and 2018-04 have 35 and 8 orders, and **0** of them have a status. Those 43 orders are the NULL group.
- Status breakdown: NULL covers 43 orders and 761.00 of the 1672.00 total.
- `customers.completed_lifetime_value` only sums amounts where `orders.status = 'completed'`. The 43 NULL-status orders never match, so their value drops out of the completed total.

The 824.00 vs 1103.00 gap is 279.00. I can't confirm from the sample that exactly 279.00 of the NULL-status amount was completed, but the mechanism explains the direction and the size of the drop. The first thing to check is which orders went from `status` to `order_status` and whether they were completed.

**Why every test passed:** `accepted_values` in dbt ignores NULLs, so the status test passes on these rows. No test asserts `not_null` on `status` in either `stg_orders` or `orders`. The NULLs are also invisible to `unique`, `not_null(order_id)`, and the `orders.amount` tests, which is why `sum(orders.amount)` is still correct at 1672.00.

**Ruled out:** `orders.amount` is correct, and `daily_revenue` and `region_revenue` do not reference status, so they are not affected by this bug.

## 2. Fix

First confirm that `order_status` holds the same vocabulary for the affected rows, and that it agrees with `status` wherever both are populated. Run this against the warehouse:

```sql
select status, order_status, count(*) as n, sum(amount) as amount
from raw_orders o
join raw_payments p on p.order_id = o.id
group by 1, 2 order by 1, 2;
```

(The `amount` column needs `/ 100` to match the staging model.) If the values agree, fix the staging model so it coalesces the two columns:

```sql
-- jaffle_shop/models/staging/stg_orders.sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,
        ordered_at,
        cast(ordered_at as date) as order_date,
        coalesce(status, order_status) as status

    from source

)
```

Alternatively, backfill `raw_orders.status` from `order_status` in the seed or source data. The staging fix is the one I'd prefer, because it keeps the raw data untouched and it fixes both the mart tables and any future rebuild.

After the change, `completed_lifetime_value` should return to 1103.00 if last week's report used the same orders and the same status values. Rebuild with `dbt build --select stg_orders+` so `customers` and `orders` are refreshed.

## 3. Test that would have caught it

Add `not_null` on `status` in the staging schema, and in the marts schema as well. The staging version catches the problem at its source:

```yaml
# jaffle_shop/models/staging/schema.yml
  - name: stg_orders
    columns:
      - name: status
        tests:
          - not_null
          - accepted_values:
              arguments:
                values: ['placed', 'shipped', 'completed', 'return_pending', 'returned']
```

```yaml
# jaffle_shop/models/schema.yml, under orders.columns
      - name: status
        tests:
          - not_null
          - accepted_values:
              arguments:
                values: ['placed', 'shipped', 'completed', 'return_pending', 'returned']
```

With this in place, the build fails on the 43 NULL-status rows. It would also have shown the problem before finance did.

A stronger guard is a singular test that reconciles the completed total against the raw data, because `not_null` alone would not catch a source that stops populating `status` for a different reason. For example, a test in `tests/` that fails if any `raw_orders` row has both `status` and `order_status` NULL, or if the two columns disagree where both are populated.

**Not verified:** I could not see the seed CSVs, so I have not confirmed which column the source treats as authoritative. The fix above assumes `order_status` is the correct value for the affected rows, and the first query is there to check that assumption.
