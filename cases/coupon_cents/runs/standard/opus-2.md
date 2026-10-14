claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 44 s | USD 0.210 | 2026-10-14 | project ad0b16b | prompt 0f146dda0451

# Diagnosis: coupon payments loaded in dollars instead of cents

## 1. Root cause

The bug is in the data in `raw_payments`, not in the SQL. `stg_payments.sql` assumes every `raw_payments.amount` is stored in cents and divides by 100:

```sql
-- `amount` is currently stored in cents, so we convert it to dollars
amount / 100 as amount
```

From March onward, the ingestion job loaded coupon payments in **dollars**, not cents. The `raw_payments` sample shows it:

| id | order_id | method | amount | stg amount |
|---|---|---|---|---|
| 27 | 24 (Jan) | coupon | 2600 | 26.00 ✅ |
| 98 | 86 (Mar) | coupon | 23 | 0.23 ❌ (should be 23.00) |
| 86 | 76 (Mar) | coupon | 2 | 0.02 ❌ |
| (order 94, Apr) | | coupon | 7 | 0.07 ❌ |

Every other amount in the sample is a whole number of cents (2000, 800, 200…). These coupon values are not.

**The numbers match exactly.** If a dollar amount *x* is divided by 100, the shortfall is 0.99·*x*:

- **March:** 18.27 + 0.99·27 = **45.00**. So $27 of March coupons were mis-scaled and $18 were correct. The shortfall of 26.73 is also exactly 622.00 − 595.27, the settlement gap.
- **April:** 0.33 + 0.99·33 = **33.00**. All $33 of April coupons were mis-scaled.
- January and February coupons are in cents, so they agree.

**Why nothing failed:**
- All the tests are `not_null`, `unique`, or `accepted_values`. A value of 0.23 passes all of them.
- `daily_revenue` is incremental. Its March and April rows (622.00 and 139.00 = 106.33 + 32.67) were built before the bad load. Only the last 3 days get rebuilt, and those days have no coupons. So `daily_revenue` still holds the **correct** figures and silently disagrees with `orders`.

`orders`, `customers` (lifetime value 1612.60, should be 1672.00), and `region_revenue` all have the error. `fees` does not, because coupons have a fee rate of 0.

## 2. Fix

**Proper fix:** correct the ingestion job so coupon amounts are written in cents like the other methods, then reload `raw_payments`. Don't add a per-method or per-date scaling hack in `stg_payments.sql`; that would bake the bug into the model.

**Stopgap data patch.** I can't see the full table, so list the rows before changing them:

```sql
-- 1. Inspect: expect only coupon rows on orders from 2018-03 onward
select p.*, o.ordered_at
from raw_payments p join raw_orders o on o.id = p.order_id
where p.amount % 100 <> 0
order by o.ordered_at;

-- 2. Patch, if the list above is exactly the mis-scaled rows
update raw_payments
set amount = amount * 100
where payment_method = 'coupon'
  and amount % 100 <> 0
  and order_id in (select id from raw_orders where ordered_at >= '2018-03-01');
```

Then rebuild everything, including a full refresh of the incremental model so it can't hold stale rows either way:

```
dbt build --full-refresh
```

Check the result: `orders` coupon totals should be 45.00 (March) and 33.00 (April), March `amount` should be 622.00, and `daily_revenue` should match `orders` month by month.

## 3. Tests that would have caught it

**a) Unit sanity on payments.** Amounts are whole cents, so a sub-dollar payment is suspicious. Save as `tests/assert_payment_amounts_plausible.sql`:

```sql
-- raw amounts are in cents; anything under 1 dollar after conversion is almost certainly a units error
select payment_id, order_id, payment_method, amount
from {{ ref('stg_payments') }}
where amount < 1
```

If `dbt_utils` is installed, you can use `dbt_utils.accepted_range` with `min_value: 1` on `stg_payments.amount` instead.

**b) Reconciliation between models.** This would have failed on the very next run. Save as `tests/assert_daily_revenue_matches_orders.sql`:

```sql
with o as (
    select order_date, count(*) as orders, sum(amount) as revenue
    from {{ ref('orders') }}
    group by order_date
)
select *
from o
full outer join {{ ref('daily_revenue') }} d using (order_date)
where o.orders is distinct from d.orders
   or abs(coalesce(o.revenue, 0) - coalesce(d.revenue, 0)) > 0.005
```

Test (a) catches the bad units directly. Test (b) catches any drift between `orders` and the incremental `daily_revenue`. In this incident, (b) would have flagged March as 595.27 vs 622.00.

Longer term, the best guard is a check against the external sources marketing used: a monthly coupon total vs the redemption ledger, and the monthly amount vs the processor settlement. You'd need to load those as sources first.

**Side note:** the log shows "Found … 32 data tests" but only 28 ran. The 4 seed tests on `raw_customer_regions` and `raw_payment_methods` aren't being executed in the nightly run, so check your selector.
