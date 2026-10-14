claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 46 s | USD 0.222 | 2026-10-14 | project ad0b16b | prompt 1c80550dffb3

# Root cause

There are **two separate defects**. Together they explain all three numbers.

| | Total | vs settlement (1737.00) |
|---|---|---|
| `orders.amount` | 1917.00 | **+180.00** from duplicate payments |
| `daily_revenue.revenue` | 1686.00 | **−51.00**, because the February backfill never reached the table |

### 1. Duplicate payment rows in `seeds/raw_payments.csv` (the data defect)

Last night's load re-sent payments for orders that already had them, and gave each one a **new `id`**. Order 1 is an example: it now has two 10.00 payments, so it shows 20.00. The model chain only guards uniqueness on `payment_id`, so the duplicates pass every test:

- `stg_payments` (`models/staging/stg_payments.sql`) passes them through, and `unique_stg_payments_payment_id` passes because the ids differ.
- `orders.sql` sums them in `order_payments`. That inflates `amount`, the per-method columns and `fees`.
- `customers.customer_lifetime_value` and `region_revenue` inherit the same inflation. Both total 1917.00.

The duplicates appear in every month, which is why January and March changed even though no orders were added there. Comparing `orders` with `daily_revenue` per month: Jan +74, Mar +57, Feb +49 (+100 minus the 51 of new orders), Apr 0. That adds up to 1917 − 1737 = 180.

### 2. `models/daily_revenue.sql`: the incremental filter can't see backfills (the logic defect)

```sql
where order_date >= (select max(order_date) - interval 3 day from {{ this }})
```

This window only covers the last three days before the latest date, which is in April. February is two months outside it, so the run inserted nothing for February:

- February shows 27 orders in `daily_revenue` against 33 in `orders`. The 6 backfilled orders, worth 51.00, are missing.
- Jan, Feb and Mar still hold the values from before the load. That is why `daily_revenue` happens to have no duplicates in those months.

1737 − 51 = 1686, which matches the dashboard total exactly.

I can't see the seed CSVs, so the 180/51 split is inferred from the totals. Run these two queries to confirm it before changing anything:

```sql
-- Should return the re-sent payments (including one for order_id 1), totalling 180.00
select p.*
from raw_payments p
join raw_payments o
  on  o.order_id = p.order_id
  and o.payment_method = p.payment_method
  and o.amount = p.amount
  and o.id < p.id;

-- Should return the 6 February orders, totalling 51.00
select o.order_id, o.order_date, o.amount
from orders o
left join daily_revenue d using (order_date)
where date_trunc('month', o.order_date) = date '2018-02-01'
  and (d.order_date is null or o.order_date not in (select order_date from daily_revenue));
```

If the second query is awkward, a simpler check is to compare `count(*)` per `order_date` between the two tables for February.

# Fix

**a) Data: remove the re-sent rows from `seeds/raw_payments.csv`.** Use the first query above to find them, and confirm the list with whoever owns the feed. Two payments with the same order, method and amount can be legitimate (a split payment), so don't delete blindly. Finance's 1737.00 is the check: after the fix, `sum(amount)` from `stg_payments` should equal 1737.00.

If the feed is expected to resend payments, also add a defensive dedup in staging. Only do this if the business agrees that identical (order, method, amount) rows are always duplicates:

```sql
-- models/staging/stg_payments.sql
with source as (

    select * from {{ ref('raw_payments') }}

),

deduped as (

    -- the payments feed can re-send a payment under a new id
    select *
    from source
    qualify row_number() over (partition by order_id, payment_method, amount order by id) = 1

),

renamed as (

    select
        id as payment_id,
        order_id,
        payment_method,

        -- `amount` is currently stored in cents, so we convert it to dollars
        amount / 100 as amount

    from deduped

)

select * from renamed
```

**b) Rebuild `daily_revenue` now:**

```bash
dbt seed --select raw_payments
dbt build --select stg_payments+ --full-refresh
```

**c) Stop `daily_revenue` from missing backfills.** At ~70 rows, the incremental model gains nothing, so the simplest correct fix is to make it a table:

```sql
{{ config(materialized='table') }}
```

If it has to stay incremental, it needs to filter on *when rows arrived*, not on `order_date`. The raw tables don't currently have a load timestamp such as `_loaded_at`, so that column would need to be added first. Until then, schedule a periodic `--full-refresh`.

# Tests that would have caught this

Add these singular tests under `tests/`:

```sql
-- tests/assert_no_duplicate_payments.sql
-- A re-sent payment gets a new id, so unique(payment_id) cannot catch it
select order_id, payment_method, amount, count(*) as copies
from {{ ref('stg_payments') }}
group by 1, 2, 3
having count(*) > 1
```

```sql
-- tests/assert_daily_revenue_reconciles_to_orders.sql
-- Catches incremental drift, such as backfills outside the lookback window
with o as (
    select order_date, count(*) as orders, sum(amount) as revenue
    from {{ ref('orders') }}
    group by 1
),
d as (
    select order_date, orders, revenue from {{ ref('daily_revenue') }}
)
select coalesce(o.order_date, d.order_date) as order_date,
       o.orders as expected_orders, d.orders as actual_orders,
       o.revenue as expected_revenue, d.revenue as actual_revenue
from o
full outer join d using (order_date)
where o.orders is distinct from d.orders
   or abs(coalesce(o.revenue, 0) - coalesce(d.revenue, 0)) > 0.005
```

The second test would have failed last night on 2018-02 and on every date that had a duplicate. The first would have failed on order 1.

If finance's settlement totals can be loaded as a seed or source, add a third test comparing `sum(stg_payments.amount)` per period against them. That is the only check that would catch errors that are internally consistent across every model.

One unrelated issue: `schema.yml` documents a `customers.total_order_amount` column, but the model outputs `customer_lifetime_value`. That isn't the cause here, but it's worth fixing while you're in that file.
