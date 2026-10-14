claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 31 s | USD 0.077 | 2026-10-14 | project 7d5b525+dirty

## 1. Root cause

There are two separate defects, and every test passes because none of the tests checks values or totals.

**A. Duplicate payment rows in the raw payments data (`raw_payments` → `stg_payments.sql` → `orders.sql`).**
- `raw_payments` has 131 rows for 105 orders. The last night's backfill appears to have re-sent payments that were already loaded, this time with new `id`s.
- `unique_stg_payments_payment_id` passes because the ids are new. `orders.sql` then does `sum(amount)` per order, so each re-sent payment is counted twice.
- Order 1 is the example: 20.00 in `orders`, 10.00 charged.
- `sum(orders.amount)` = `sum(stg_payments.amount)` = 1917.00, versus 1737.00 settled. The 180.00 gap is double-counting. `region_revenue` and `customers.customer_lifetime_value` read the same payments, so they are inflated too.

**B. `models/daily_revenue.sql` never picks up backfilled history.**
- Its incremental filter is `order_date >= max(order_date) in {{ this }} - 3 days`. The max date is in April, so only the last three days of April are ever rebuilt.
- Orders backfilled into February land in `orders` but never reach `daily_revenue`:
  - 2018-02-03 and 2018-02-22 are absent entirely.
  - 2018-02-08, 02-13, 02-17 and 02-26 have fewer orders than `orders`.
  - Overall, 99 orders are counted in `daily_revenue` versus 105 in `orders`.
- The January and March rows (for example 01-01 showing 10.00) also differ from `orders`. That is consistent with `daily_revenue` having been built before the duplicates arrived and never refreshed. The incremental comment about payments landing late is wrong for this model: the window is anchored to the max date in the target, not to when the data arrived.
- So the dashboard's 1686.00 is stale. It is not the 1917.00 in `orders`, and it is not the true 1737.00.

I couldn't see the seed data, so I haven't confirmed that the extra 26 payment rows are exact copies. Check that first:

```sql
select order_id, payment_method, amount, count(*) n, list(payment_id) ids
from raw_payments
group by 1,2,3 having count(*) > 1;
```

The extra rows should sum to 180.00 (18000 in cents).

## 2. Fix

1. **Remove the re-sent rows at the source.**
   - Delete the duplicate ids from `raw_payments` (the seed CSV here, the real table in production). Keep the lowest id per group.
   - Then confirm `sum(stg_payments.amount) = 1737.00`.
   - I would not add a blind `row_number()` dedupe in `stg_payments`. Two legitimate payments of the same method and amount on one order, such as two 10.00 gift cards, would be collapsed. If you want a model-level guard, key it on a processor transaction id.
2. **Fix `daily_revenue`.** It is tiny and fully derived from `orders`, so make it a plain table:

```sql
{{ config(materialized='table') }}
-- remove the is_incremental() block
```

   If it must stay incremental, filter on a load timestamp or `order_date >= current_date - N` plus a scheduled `--full-refresh`. Don't anchor to `max(order_date)` of the target.
3. Run `dbt build --full-refresh` once. Expected result: `orders` = `daily_revenue` = 1737.00 with 105 orders.

## 3. Tests that would have caught it

Add `dbt_utils` and a uniqueness test on the natural key of a payment (add a processor transaction id if you have one):

```yaml
- name: stg_payments
  tests:
    - dbt_utils.unique_combination_of_columns:
        arguments:
          combination_of_columns: [order_id, payment_method, amount]
          config: {severity: warn}   # legit same-amount splits need review
```

And singular reconciliation tests in `tests/`:

```sql
-- tests/assert_daily_revenue_matches_orders.sql
with o as (
  select order_date, count(*) n, sum(amount) amt from {{ ref('orders') }} group by 1
)
select *
from o
full join {{ ref('daily_revenue') }} d using (order_date)
where coalesce(o.n,0) <> coalesce(d.orders,0)
   or abs(coalesce(o.amt,0) - coalesce(d.revenue,0)) > 0.005
```

```sql
-- tests/assert_orders_total_matches_settlement.sql
-- seed/source of processor settlement totals per month
select s.month, s.settled, o.amt
from {{ ref('processor_settlement') }} s
join (select strftime(order_date,'%Y-%m') month, sum(amount) amt
      from {{ ref('orders') }} group by 1) o using (month)
where abs(s.settled - o.amt) > 0.005
```

The first test would have flagged the 16 mismatching days. The second would have caught the 180.00 overstatement the night of the load.
