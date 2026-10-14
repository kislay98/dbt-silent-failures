claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 49 s | USD 0.177 | 2026-10-14 | project 8ee5734 | prompt d0c0d63b3bb2

# Root cause

There are two upstream data changes. The models are not wrong in themselves. The tests were loosened in a way that let both changes through.

**The numbers reconcile exactly:**

- Missing amount since January is 1103 − 967 = **136**. Missing amount for March is 279 − 143 = **136**. So the whole gap is in March.
- March `delivered` orders total **134.02**. That figure includes order 76, which shows **0.02** paid by coupon.
- If order 76's coupon is really **2.00**, delivered revenue is **136.00**, and 143 + 136 = **279**. Since January, 967 + 136 = **1103**.
- The customer sample confirms it. For each customer, `lifetime − completed` equals exactly their March `delivered` order:

  | Customer | Lifetime − completed | March delivered order |
  |---|---|---|
  | 3 | 19 | order 69 |
  | 8 | 26 | order 70 |
  | 19 | 3 | order 73 |
  | 36 | 28 | order 66 |
  | 53 | 16 | order 68 |
  | 79 | 23 | order 67 |

### 1. `raw_orders.status`: the source now writes `delivered` where it used to write `completed`

- From about 2018-03-10, finished orders arrive as `delivered`.
- `models/customers.sql` (`completed_amount`) and anything filtering `orders.status = 'completed'` only match the literal `'completed'`. These orders drop out.
- The tests didn't catch it because `'delivered'` was added to `accepted_values` in both `models/staging/schema.yml` (`stg_orders.status`) and `models/schema.yml` (`orders.status`). The new value was whitelisted without updating the logic that interprets statuses.

### 2. `raw_payments.amount`: coupon payments are being loaded in dollars, not cents

- From about 2018-03-20, coupon payments arrive in dollars instead of cents.
- `models/staging/stg_payments.sql` divides every amount by 100, so a $2 coupon becomes 0.02.
- Affected orders in the sample: 76, 81, 86, 92 (15.02), 94, 95, with coupon amounts of 0.02, 0.23, 0.07 and 0.24. These should be 2, 23, 7 and 24 dollars. Every other payment in the data is a whole dollar amount.
- Only order 76 is completed, so this accounts for 2.00 of the gap. However, it also understates `amount`, `fees`, `customer_lifetime_value`, `region_revenue` and `daily_revenue` for the other orders.

# Fix

**Status:** normalize it in staging and remove `delivered` from the accepted values, so the next unknown status fails the build. First confirm with the shop or source owner that `delivered` means the same as `completed`. The admin totals suggest it does.

```sql
-- models/staging/stg_orders.sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,
        ordered_at,
        cast(ordered_at as date) as order_date,
        -- source renamed 'completed' to 'delivered' from 2018-03-10
        case when status = 'delivered' then 'completed' else status end as status

    from source

)
```

```yaml
# models/staging/schema.yml and models/schema.yml: drop 'delivered'
values: ['placed', 'shipped', 'completed', 'return_pending', 'returned']
```

**Coupon units:** fix this in the ingestion job and backfill, because `raw_payments` has no load timestamp to key a staging workaround on. The one-off backfill would look like this. Check which rows match before running it:

```sql
-- inspect first
select * from raw_payments where payment_method = 'coupon' and amount < 100;
-- then
update raw_payments set amount = amount * 100
where payment_method = 'coupon' and amount < 100;
```

After both changes, run `dbt build --full-refresh -s daily_revenue+` together with the normal build. `daily_revenue` is incremental and only rebuilds the last 3 days, so the March and April rows would otherwise stay wrong.

# Tests that would have caught it

1. **The original `accepted_values` without `'delivered'`.** That test would have failed. The rule should be that any change to that list also requires updating the status logic. To make this explicit, map each status to a revenue bucket in a seed (`order_status_map`) and add a `relationships` test from `stg_orders.status` to it. A new status then can't pass unless someone decides whether it counts as completed.

2. **A unit sanity check on payments.** Add `tests/assert_payment_amounts_in_cents.sql`:

   ```sql
   -- raw amounts are cents; prices are whole dollars, so anything not a multiple of 100 is a unit error
   select *
   from {{ ref('raw_payments') }}
   where amount % 100 <> 0
   ```

   Use `severity: warn` if fractional prices are legitimately possible. A `>= 100` minimum also works.

3. **A reconciliation test.** Add a singular test asserting that `sum(customers.completed_lifetime_value)` equals `sum(orders.amount) where status = 'completed'`, and that both match the admin's completed-revenue export if it can be loaded as a source. None of the current tests check any totals, which is why every test passed while the totals were off by 136.
