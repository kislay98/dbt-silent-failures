claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 63 s | USD 0.010 | 2026-10-14 | project ad0b16b | prompt 0f146dda0451

## 1. Root cause

**Coupon payments are stored in mixed units in the raw payments table, and `stg_payments.sql` divides every row by 100.**

- `jaffle_shop/models/staging/stg_payments.sql`: `amount / 100 as amount`, with the comment "`amount` is currently stored in cents". That holds for most rows, but not for all of them.
- The mismatch is exact. Two coupon payments appear to have been loaded as whole dollars (27 and 33) instead of cents (2700 and 3300). The staging model reads them as cents, so each is understated by 99%:

| Month | Ledger coupon | `orders.coupon_amount` | Gap | Gap if two rows are 27 and 33 dollars |
|---|---|---|---|---|
| March | 45.00 | 18.27 | 26.73 | 0.99 × 27 = 26.73 ✓ |
| April | 33.00 | 0.33 | 32.67 | 0.99 × 33 = 32.67 ✓ |

- The settlement figure agrees with the corrected numbers: 595.27 − 0.27 + 27.00 = 622.00.
- `daily_revenue` already shows the corrected totals (March 622.0, April 139.0). It is an incremental model that only rebuilds the last three days, so it kept the values from before the bad rows arrived. `orders` is rebuilt from current raw data, so it shows the wrong values. The disagreement between the two tables is the only in-repo signal that something changed.
- Why the tests pass: nothing checks the units or reconciles totals. `not_null` and `accepted_values` are satisfied by 0.27 just as well as by 27.00. No test compares `daily_revenue` to `orders` or any total to an external source.

The 27 and 33 values are inferred from the arithmetic, and I have no warehouse access to confirm them. Check with the query in section 2 before changing anything.

## 2. Fix

**Fix the producer, not the model.** A cent value of 27 and a dollar value of 27 look identical in the row, so no reliable rule in `stg_payments.sql` can tell them apart. Adding a heuristic there would hide the problem.

Step 1. Confirm which rows are affected:

```sql
select p.id, p.order_id, p.payment_method, p.amount, o.ordered_at
from raw_payments p
join raw_orders o on o.id = p.order_id
where p.payment_method = 'coupon'
  and p.amount in (27, 33)
order by o.ordered_at;
```

Also check the ingestion job's source for those payment IDs. If the loader is writing dollars for some rows, find the code path that changed.

Step 2. Correct the two rows to cents (2700 and 3300) at the source and reload `raw_payments`.

Step 3. Rebuild:

```bash
dbt build --full-refresh -s daily_revenue+
```

The `--full-refresh` matters because the incremental window will not touch March. The expected results are March 622.00 and April 139.00 for `daily_revenue`, and `orders.coupon_amount` of 45.00 and 33.00.

## 3. A test that would have caught it

Two tests, in order of value.

**a) Reconcile payments to the processor settlement (catches the root cause).** Load the settlement totals as a seed, `seeds/raw_processor_settlements.csv` with `month` and `settled_amount`, and add a singular test:

```sql
-- tests/assert_payments_match_settlement.sql
with payments_by_month as (
    select date_trunc('month', o.order_date) as month,
           sum(p.amount) as payments_amount
    from {{ ref('stg_payments') }} p
    join {{ ref('stg_orders') }} o on p.order_id = o.order_id
    group by 1
),
settlements as (
    select cast(month as timestamp) as month, settled_amount
    from {{ ref('raw_processor_settlements') }}
)
select s.month, s.settled_amount, p.payments_amount
from settlements s
join payments_by_month p using (month)
where abs(s.settled_amount - p.payments_amount) > 0.005
```

Today this returns March (622.00 vs 595.27) and April. It only works if the settlement feed is independent of the ingestion job that produced the bad rows.

**b) Reconcile `daily_revenue` to `orders` (catches the incremental drift).**

```sql
-- tests/assert_daily_revenue_matches_orders.sql
with orders_by_day as (
    select order_date, sum(amount) as revenue
    from {{ ref('orders') }}
    group by 1
),
daily as (
    select order_date, revenue from {{ ref('daily_revenue') }}
)
select coalesce(o.order_date, d.order_date) as order_date,
       o.revenue as orders_revenue,
       d.revenue as daily_revenue
from orders_by_day o
full outer join daily d using (order_date)
where abs(coalesce(o.revenue, 0) - coalesce(d.revenue, 0)) > 0.005
```

This fails today on the March and April rows. It would have turned the "all green" build into a failure the night the rows changed.

## Other things I noticed

- `daily_revenue` only rebuilds the last three days, so any upstream correction older than that is silently ignored. Either run a periodic full refresh or rely on test (b).
- `raw_customer_regions` has an `updated_at` column, which suggests history. `region_revenue` joins to it on `customer_id` with no uniqueness test on the seed. If a customer has two rows, revenue is double-counted. Add a `unique` test on `customer_id` in the seed.
- `schema.yml` documents `customers.total_order_amount`, but the model has no such column. The model produces `customer_lifetime_value`.
