claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 50 s | USD 0.180 | 2026-10-14 | project 8ee5734 | prompt d0c0d63b3bb2

# Root cause: a new `delivered` status isn't counted as completed, and recent coupon amounts are 100× too small

Your figures are consistent with each other, which narrows it down:

- Admin since January is 1103.00. `orders` shows completed amounts of 424 (Jan) + 400 (Feb) + 279 (Mar) = 1103.
- Our completed revenue is 967.00, which is 424 + 400 + 143. Both totals are short by exactly **136.00**, and all of it is in March.
- The missing 136 is the March `delivered` orders: 66, 67, 68, 69, 70, 73, 75 and 76, which come to 28 + 23 + 16 + 19 + 26 + 3 + 19 + **2.00** = 136.
- `orders` shows that bucket as 134.02, not 136, because of a second, smaller bug in order 76.

## 1. Root cause

**Bug A: status rename upstream (the main issue, 134.00)**

- From about 2018-03-10 (order 66 onward), the shop sends `delivered` for orders it previously called `completed`.
- `models/customers.sql` only counts `orders.status = 'completed'`, so these orders drop out of `completed_lifetime_value`. The sample confirms it: customers 19 and 36 each have only a delivered order, and both show `completed_lifetime_value = 0.0`.
- The tests didn't catch it because someone added `'delivered'` to the `accepted_values` lists in `models/schema.yml` and `models/staging/schema.yml`. That turned the one test that would have failed into a pass.

**Bug B: coupon payments stored in dollars instead of cents (2.00 in completed, more elsewhere)**

- `models/staging/stg_payments.sql` divides `amount` by 100, assuming cents.
- Recent coupon rows in `raw_payments` are already in dollars, so a $2 coupon becomes 0.02. The affected orders are 76, 81, 86, 92, 94 and 95, with coupon amounts like 0.02, 0.23, 0.07 and 0.24.
- Every other amount in this shop is a whole number of dollars. The sub-dollar values appear only in coupon rows from mid-March on.
- This doesn't touch Jan/Feb, but it understates order 76's completed revenue by 1.98. It also corrupts `amount`, `fees`, `customer_lifetime_value`, `daily_revenue` and `region_revenue` for every affected order.

## 2. Fix

**A. Normalize the status once, in staging.** `models/staging/stg_orders.sql`:

```sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,
        ordered_at,
        cast(ordered_at as date) as order_date,
        -- the shop renamed 'completed' to 'delivered' around 2018-03-10
        case when status = 'delivered' then 'completed' else status end as status,
        status as source_status

    from source

)
```

Then remove `'delivered'` from both `accepted_values` lists, so the next upstream rename fails the build instead of passing silently.

**B. Fix the coupon units at the source.** The ingestion job should write coupons in cents like every other payment method; then backfill the affected rows. First confirm where the units change, because I can't see the raw rows:

```sql
select id, order_id, amount from raw_payments where payment_method = 'coupon' order by id;
```

Then backfill, using the cutover id from that query:

```sql
update raw_payments set amount = amount * 100
where payment_method = 'coupon' and id >= <first_bad_id>;
```

**C. Rebuild everything, including the incremental model.** `daily_revenue` only rebuilds its last three days, so the March rows would otherwise stay stale:

```
dbt build --full-refresh
```

After the rebuild, these should hold:
- Completed revenue for March in `orders` is 279.00.
- `sum(customers.completed_lifetime_value)` is 1103.00.

## 3. Tests that would have caught this

1. **Keep `accepted_values` strict.** The original list without `delivered` would have failed on the first run after the rename. Any change to that list should get the same review as a logic change.

2. **No sub-dollar payments.** This would have caught Bug B. Add `tests/assert_payments_whole_dollars.sql`:
   ```sql
   -- all prices and coupons in this shop are whole dollars; fractional amounts mean a units error upstream
   select payment_id, payment_method, amount
   from {{ ref('stg_payments') }}
   where amount <> round(amount)
   ```
   If fractional prices ever become legitimate, a looser `amount >= 1` check still catches a 100× scaling error.

3. **Completed orders still appear each month.** This catches any status rename that empties a bucket. Add `tests/assert_completed_orders_each_month.sql`:
   ```sql
   -- every month older than the fulfilment window should have completed orders
   with monthly as (
       select date_trunc('month', order_date) as month,
              count(*) filter (where status = 'completed') as completed
       from {{ ref('orders') }}
       where order_date < (select max(order_date) from {{ ref('orders') }}) - interval 30 day
       group by 1
   )
   select * from monthly where completed = 0
   ```
   The strongest check is reconciling monthly completed revenue against an export of the admin's figures, if one can be loaded.

## Other things I noticed

These didn't cause this discrepancy, but they're worth checking:

- **Seed tests didn't run.** dbt found 32 data tests but ran 28, so the 4 tests on `raw_customer_regions` and `raw_payment_methods` aren't executing.
- **Possible double counting from duplicate keys.** Neither seed has a uniqueness test.
  - `raw_customer_regions` has an `updated_at` column, which suggests multiple rows per customer. That would duplicate revenue in `region_revenue`.
  - A duplicate `payment_method` row in `raw_payment_methods` would double-count `fees`.
  - Adding `unique` tests on `customer_id` and `payment_method` would expose both.
