claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 31 s | USD 0.102 | 2026-10-14 | project ad0b16b | prompt 1c80550dffb3

## 1. Root cause

There are two separate defects, and the tests are blind to both.

**a) Duplicated payments in `seeds/raw_payments.csv` (overstates `orders.amount` by 180.00).**
- Every model downstream of `stg_payments` is overstated by the same amount: `orders`, `customers` lifetime value and `region_revenue` all total 1917.00, against 1737.00 settled.
- So the extra 180.00 comes from the raw payments, not from any join fan-out. The joins are 1:1, and `raw_payment_methods` has 4 rows.
- `raw_payments` has 131 rows. The IDs I can see at 114 and above (118, 121, and so on) look like the rows added by the backfill. Payment 121 is a bank transfer for order 10, a January order.
- The backfill reloaded payments that already existed under new `id` values. The only uniqueness test is on `payment_id`, so these duplicates pass `unique_stg_payments_payment_id`. Order 1 showing 20.00 instead of 10.00 is the same thing: its 10.00 credit card payment exists twice.
- I can't see the seed data, so I haven't confirmed which rows are duplicates. Run the query below to check that they sum to 180.00.

**b) `models/daily_revenue.sql` is incremental and only rebuilds the last 3 days (dashboard 1686.00).**
- The filter is `order_date >= max(order_date) - 3 days`. The max date is in April, so anything older is never recomputed.
- February orders backfilled last night were never picked up. February has 27 orders in `daily_revenue` against 33 in `orders`.
- January and March have the right order counts but lower revenue (496 vs 570, 622 vs 679). Payments landed after those days were first built and were never refreshed.
- Only April matches, at 153.
- The model's comment assumes payments arrive within 2 days. A backfill breaks that assumption.

## 2. Fix

**Data:** find the duplicates, confirm them against the processor, and delete them from the seed.

```sql
select order_id, payment_method, amount, count(*) n, list(id order by id) ids
from raw_payments
group by 1,2,3 having count(*) > 1
order by order_id;
```

The surplus rows (all but the lowest `id` in each group) should sum to 180.00 once divided by 100. If any are genuine repeat payments, keep those.

**Code:** make `daily_revenue` a plain table. It's cheap, and it removes the stale-window failure entirely.

```sql
{{ config(materialized='table') }}
```

Delete the `is_incremental()` block. If it has to stay incremental, key the lookback on a load timestamp rather than `order_date`. Either way, run `dbt build --full-refresh -s daily_revenue+` once now. After the dedupe, `daily_revenue` should total 1737.00.

## 3. Tests that would have caught it

Tests on the data (add the first two to `schema.yml`):

```yaml
# stg_payments
- dbt_utils.unique_combination_of_columns:
    arguments:
      combination_of_columns: [order_id, payment_method, amount]
    config: {severity: warn}   # repeat payments can be legitimate
```

```yaml
# daily_revenue
- dbt_utils.equal_rowcount:
    arguments: {compare_model: ref('orders')}   # not suitable; use the singular test below
```

Skip the second one and use these singular tests instead.

`tests/assert_daily_revenue_matches_orders.sql` catches the incremental staleness:

```sql
select 'total' as k
from (select sum(revenue) r from {{ ref('daily_revenue') }}) d,
     (select sum(amount)  a from {{ ref('orders') }}) o
where abs(d.r - o.a) > 0.005
union all
select cast(d.order_date as varchar)
from {{ ref('daily_revenue') }} d
full join (select order_date, count(*) n, sum(amount) a
           from {{ ref('orders') }} group by 1) o using (order_date)
where d.orders is distinct from o.n or abs(coalesce(d.revenue,0) - coalesce(o.a,0)) > 0.005
```

`tests/assert_orders_reconcile_to_settlement.sql` catches the duplicates. It needs the finance settlement total loaded as a seed, `raw_settlement(period_start, period_end, amount)`:

```sql
select s.period_start, s.amount as settled, sum(o.amount) as modelled
from {{ ref('raw_settlement') }} s
join {{ ref('orders') }} o on o.order_date between s.period_start and s.period_end
group by 1, 2
having abs(sum(o.amount) - s.amount) > 0.005
```

The duplicate check is only a warning. The settlement test is the one that would have failed last night, and `dbt build` would have stopped on it.
