claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 37 s | USD 0.109 | 2026-10-14 | project ad0b16b | prompt 1c80550dffb3

I couldn't see the seed rows, so the cause of the 180.00 overage is inferred from the numbers and needs one query to confirm. I haven't run anything.

## 1. Root cause

There are two independent problems, and every test passes because none of them checks totals.

**A. `seeds/raw_payments` (read via `models/staging/stg_payments.sql`): re-delivered payments with new ids.**
- The backfill delivered payments for existing orders again under new `id`s. Example: payment `121` is a 26.00 bank transfer on order 10, whose other payments carry low ids.
- Order 1 shows 20.00 against a real charge of 10.00, which fits the same pattern: the same payment appears twice under different ids.
- `unique_stg_payments_payment_id` passes because the ids differ. `orders.sql` and `customers.sql` just `sum(amount)`, so the duplicates flow straight through. `orders`, `customers` and `region_revenue` all total 1917.00, which is 180.00 over the 1737.00 settlement.
- Nothing in the project compares payments against the processor settlement.

**B. `models/daily_revenue.sql`: the incremental window is the wrong one.**
- The filter is `order_date >= max(order_date) - 3 days`, which is relative to the newest order date. It is not relative to when the data arrived.
- The comment says payments can land late. But the window only covers the last three days of order dates, so anything arriving for older orders is never rebuilt.
- The mismatch against `orders`, by month:

| Month | `orders` | `daily_revenue` | Gap |
|---|---|---|---|
| Feb | 33 orders, 515 | 27 orders, 415 | 6 orders, 100 |
| Jan | 570 | 496 | 74 |
| Mar | 679 | 622 | 57 |
| Apr | 153 | 153 | 0 |

- The 6 missing February orders are the backfilled ones (ids 100–105). The Jan and Mar gaps have equal order counts, so they are payments that landed on already-loaded orders.
- The gaps sum to 231, which is exactly 1917 − 1686. The dashboard is stale on top of being inflated.
- Only April, which sits inside the window, matches.

## 2. Fix

**Confirm the duplicates first.** Run this and check that the surplus is 180.00 in total, and that order 1 is among the rows:

```sql
select order_id, payment_method, amount, count(*) as n, list(id order by id) as ids
from raw_payments
group by 1, 2, 3
having count(*) > 1
order by 1;
```

A single order could legitimately have two identical payments, so don't dedupe on content unless the surplus matches 180.00. If it doesn't, get the processor transaction id from the feed and dedupe on that.

If it matches, dedupe in `stg_payments.sql`, keeping the first delivery:

```sql
renamed as (

    select
        id as payment_id,
        order_id,
        payment_method,

        -- `amount` is currently stored in cents, so we convert it to dollars
        amount / 100 as amount

    from source

    -- the backfill re-delivered payments under new ids; keep the first delivery
    qualify row_number() over (
        partition by order_id, payment_method, amount
        order by id
    ) = 1

)
```

The better long-term fix is to dedupe at the source feed.

**Fix `daily_revenue`.** It has 69 rows, so incremental buys nothing. Make it a plain table:

```sql
{{ config(materialized='table') }}
```

Then delete the `is_incremental()` block. If it has to stay incremental, filter on a load timestamp rather than `order_date`.

Run `dbt build --full-refresh -s daily_revenue+` once to clear the stale history. After that, `orders`, `customers`, `region_revenue` and `daily_revenue` should all total 1737.00.

## 3. Tests that would have caught it

Add a singular test, `tests/assert_revenue_reconciles.sql`, for the internal consistency:

```sql
-- daily_revenue must equal orders, and orders must equal payments
select 'daily_revenue_vs_orders' as check_name,
       (select sum(revenue) from {{ ref('daily_revenue') }}) as got,
       (select sum(amount) from {{ ref('orders') }}) as expected
where abs(got - expected) > 0.005
union all
select 'orders_vs_payments',
       (select sum(amount) from {{ ref('orders') }}),
       (select sum(amount) from {{ ref('stg_payments') }})
where abs(got - expected) > 0.005
```

Add a duplicate-payment check on the staging model in `models/staging/schema.yml`. This needs `dbt_utils`:

```yaml
  - name: stg_payments
    tests:
      - dbt_utils.unique_combination_of_columns:
          arguments:
            combination_of_columns: [order_id, payment_method, amount]
```

Add the finance anchor as a seed, `seeds/processor_settlement.csv` with `period_start`, `period_end` and `amount`. Then add a test that fails when `sum(orders.amount)` differs from the settled figure. This is the only check that catches duplicates when the processor and the warehouse disagree.
