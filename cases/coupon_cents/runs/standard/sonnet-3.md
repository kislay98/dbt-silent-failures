claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 34 s | USD 0.102 | 2026-10-14 | project ad0b16b | prompt 0f146dda0451

## 1. Root cause

The raw coupon payments for March and April are in whole dollars, not cents. Everything else in `raw_payments.amount` is in cents, and `models/staging/stg_payments.sql` divides all of it by 100 (`amount / 100 as amount`). So the dollar-denominated coupon rows come out 100x too small.

The numbers are consistent with this, assuming `c` is the coupon total that is correct and `d` is the total that arrived in dollars:

| Month | Ledger | `orders.coupon_amount` | Implied `c` | Implied `d` | Check |
|---|---|---|---|---|---|
| March | 45.00 | 18.27 | 18 | 27 | 18 + 27/100 = 18.27 |
| April | 33.00 | 0.33 | 0 | 33 | 0 + 33/100 = 0.33 |

- **Settlement gap:** the March settlement gap (622.00 − 595.27 = 26.73) is exactly the March coupon shortfall (45.00 − 18.27). April's `daily_revenue` is 139.00 against 106.33 in `orders`, a gap of 32.67, which matches too. So `orders.amount` is understated by the same rows.
- **Sample rows:** `raw_payments` has coupon rows like id 98 (order 86, amount 23, so 0.23 instead of 23.00) and id 76 (amount 2). Order 94 shows `coupon_amount` 0.07 for an April order.
- **Why `daily_revenue` is right but `orders` is wrong:** `models/daily_revenue.sql` is incremental and only rebuilds the last three days. Its March and April rows (622.00 and 139.00, which match settlement) are stale values from before the coupon rows were loaded in dollars. Those rows were later reloaded or restated, and the `orders` table was rebuilt from them. Because the lookback window is short, the two models now disagree and nothing flags it.
- **Why every test passed:** the tests only check `not_null`, `unique` and `accepted_values`. A coupon of 0.23 instead of 23.00 passes all of them. There are no reconciliation or magnitude checks.

I could not see the seed data, so I inferred this from the aggregates. Confirm it with the query in step 2 before changing anything.

## 2. Fix

1. Find the affected rows. The expected result is a sum of 27 for March and 33 for April.
   ```sql
   select date_trunc('month', o.order_date) m, p.id, p.order_id, p.amount
   from raw_payments p join raw_orders o on o.id = p.order_id
   where p.payment_method = 'coupon' and o.ordered_at >= '2018-03-01' and p.amount < 100
   order by 1, 2;
   ```
2. Correct them at the source, in the ingestion job. Scale the rows to cents once the sums match:
   ```sql
   update raw_payments set amount = amount * 100
   where payment_method = 'coupon' and amount < 100
     and order_id in (select id from raw_orders where ordered_at >= '2018-03-01');
   ```
   The `amount < 100` filter is a heuristic, so use the step 1 list to confirm it. Also fix the ingestion job or feed so it stops emitting dollars for coupons.
3. Rebuild with `dbt build --full-refresh -s daily_revenue+ orders+ customers region_revenue`. A full refresh is needed because the incremental model won't touch old dates on its own.

After the rebuild, March coupon should be 45.00, April 33.00, and March `amount` should be 622.00, matching settlement.

I'd also widen or remove the three-day lookback in `daily_revenue`, or make it a plain table. At this data size the incremental model buys nothing and it hides restatements.

## 3. Tests that would have caught it

**Singular test** `tests/assert_daily_revenue_matches_orders.sql`. It would have failed on March and April as soon as the data was restated:
```sql
select o.order_date, o.revenue as orders_revenue, d.revenue as daily_revenue
from (select order_date, sum(amount) revenue from {{ ref('orders') }} group by 1) o
full join {{ ref('daily_revenue') }} d using (order_date)
where abs(coalesce(o.revenue,0) - coalesce(d.revenue,0)) > 0.005
```

**Unit sanity test** on staging, since coupons are never legitimately a few cents. Put it in `tests/assert_coupon_amounts_in_cents.sql` and raise `severity` to `error` once the feed is fixed:
```sql
{{ config(severity='warn') }}
select payment_id, amount from {{ ref('stg_payments') }}
where payment_method = 'coupon' and amount < 1
```

**Month-over-month coupon share check.** Ideally also load the marketing ledger and settlement totals as a source and compare per month. That is the only check that catches a unit error that stays internally consistent.
