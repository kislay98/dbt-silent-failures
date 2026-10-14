claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 48 s | USD 0.218 | 2026-10-15 | project ad0b16b | prompt 75e42f540dd8

# Diagnosis: bank transfer amounts in `raw_payments` changed units mid-March

## 1. Root cause

The problem is in the data, not the SQL. **`raw_payments` (`seeds/raw_payments.csv`, loaded by the ingestion job) holds bank_transfer amounts that are 100× too large from mid-March onward.**

`stg_payments.sql` divides `amount` by 100 because the column is documented as cents. That holds for every credit_card, coupon and gift_card row, and for bank transfers up to about order 67. From some point in March, the bank_transfer rows (processor `anz` in `raw_payment_methods`) arrive in ten-thousandths of a dollar instead of cents:

| order | raw `amount` | after `/100` | actually paid |
|---|---|---|---|
| 57 (2018-03-01) | 200 | 2.00 | 2.00 ✅ |
| 67 | 400 | 4.00 | 4.00 ✅ |
| 73 | 3,000,000 | 30,000.00 | 3.00 ❌ |
| 92 | 15,000,000 | 150,000.00 | 15.00 ❌ |
| 97 | 14,000,000 | 140,000.00 | 14.00 ❌ |

The error is exactly a factor of 10,000 in dollars, and it matches finance's numbers:

- **April:** 390,000 = 39 × 10,000.
- **March:** 1,140,048 = 48 (correct early-March transfers) + 114 × 10,000 (inflated late-March transfers). 48 + 114 = 162, which is the processor statement total.

The models are consistent with each other:

- The fee join is 1:1, so fees are not double-counted.
- `region_revenue` sums to the same 1,531,519 as `customers`.
- `fees` is inflated only because it is computed from the inflated amounts (0.5% × 260,000 = 1,300 for order 78).

Two related problems made this worse:

- **No test checks magnitude.** Every test is `not_null`, `unique` or `accepted_values`, so a 10,000× value passes all of them.
- **`daily_revenue` is stale and now wrong in a different way.** It shows March = 622 (correct, built before the bad rows arrived) and April = 240,115 (partly bad). Because it is incremental and only rebuilds the last 3 days, fixing the source will not repair it without a full refresh.

Also note that the build found 32 tests but ran only 28. The seeds and their 4 tests were excluded from the nightly run, so even the seed-level checks never execute.

## 2. Fix

**Fix the data at the source.** Most likely the ANZ feed or its loader changed to 4 implied decimals. Fix the ingestion so `amount` is in cents again, then correct the rows already loaded.

First, find the exact cutoff:

```sql
select p.id, p.order_id, o.ordered_at, p.amount
from raw_payments p
join raw_orders o on o.id = p.order_id
where p.payment_method = 'bank_transfer'
order by o.ordered_at;
```

Then rescale everything from the first bad payment id onward. Use the id you found from the query; it is about payment 84 / order 73:

```sql
update raw_payments
set amount = amount / 100
where payment_method = 'bank_transfer'
  and id >= <first_bad_payment_id>;
```

If `raw_payments` really is the seed, make the same edit in `seeds/raw_payments.csv` and run `dbt seed -s raw_payments`. Then rebuild, forcing the incremental model to recompute every day:

```bash
dbt build --full-refresh -s stg_payments+
```

After this, `orders` should show bank_transfer = 162.00 for March and 39.00 for April, and `daily_revenue` should match `orders` month by month.

I would not add a "divide by 10,000 when the value is huge" heuristic in `stg_payments`. It hides the next unit change instead of surfacing it.

## 3. Tests that would have caught this

**Range test on payment amounts.** `tests/assert_payment_amount_in_range.sql`:

```sql
-- Amounts are dollars after staging; individual payments are a few dollars to tens of dollars
select payment_id, order_id, payment_method, amount
from {{ ref('stg_payments') }}
where amount < 0
   or amount > {{ var('max_payment_amount', 1000) }}
```

This fails on orders 73, 78, 90, 92, 97 and 98.

**Reconciliation test between `daily_revenue` and `orders`.** `tests/assert_daily_revenue_matches_orders.sql` catches incremental drift:

```sql
with o as (
    select order_date, count(*) as orders, coalesce(sum(amount), 0) as revenue
    from {{ ref('orders') }}
    group by 1
)
select *
from o
full outer join {{ ref('daily_revenue') }} d using (order_date)
where o.orders is distinct from d.orders
   or abs(coalesce(o.revenue, 0) - coalesce(d.revenue, 0)) > 0.01
```

This would have flagged both the inflated April days and the March days that `daily_revenue` never rebuilt.

**Stronger option.** If finance can provide the processor statement totals as a seed, a monthly reconciliation of `orders` per payment method against that seed is the definitive check.

**Run the seed tests.** Also make sure the nightly job runs the seed tests, or define these checks as source tests if the raw tables are really ingestion-owned sources.
