claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 50 s | USD 0.229 | 2026-10-14 | project ad0b16b | prompt 1c80550dffb3

# Two separate bugs: duplicated payments and a stale incremental model

There are two independent problems. Neither breaks a test, because the tests only check keys and nulls. Nothing checks amounts or totals.

| Number | Value | Why it's wrong |
|---|---|---|
| Settlement (truth) | 1737.00 | — |
| `sum(orders.amount)` | 1917.00 | +180.00 of duplicated payment rows |
| `daily_revenue` total | 1686.00 | Stale. The incremental model never re-processed the backfilled dates. |

## 1. Root cause

### A. Duplicate rows in `raw_payments` (the `raw_payments` seed)

Last night's load re-inserted payments that already existed, with **new `id`s**. `unique_stg_payments_payment_id` checks only `id`, so it passes. You can see this in the sample:

- Payment `121` is order 10, bank_transfer, 2600. The original jaffle_shop payment `11` has the same order, method and amount.
- Order 1 shows 20.00. Its original 10.00 credit-card payment (id 1) has a copy, so it is counted twice.

The duplicates hit old orders as well as February ones (orders 1, 10 and 62 are visible in the sample). They inflate every model built on `stg_payments`:

- `orders.amount` and the per-method amounts
- `fees`
- `customers.customer_lifetime_value` (sums to 1917)
- `region_revenue` (sums to 1917)

The extra 1917 − 1737 = 180.00 is exactly these duplicate rows. This query confirms it:

```sql
select order_id, payment_method, amount, count(*) as n, list(id order by id) as ids,
       (count(*) - 1) * amount / 100 as excess
from raw_payments
group by all
having count(*) > 1;
-- sum(excess) should be 180.00
```

### B. `models/daily_revenue.sql` misses backfilled dates

The incremental filter is:

```sql
where order_date >= (select max(order_date) - interval 3 day from {{ this }})
```

This rebuilds only the last 3 days (about 2018-04-06 onward). Backfilled February orders, and new payments on older orders, have earlier `order_date`s, so they are never re-aggregated.

- February shows 27 orders in `daily_revenue` versus 33 in `orders`. The 6 backfilled orders (ids 100–105) are missing.
- January and March are also behind `orders`.
- April matches because it is inside the window.

So the dashboard happens to exclude the duplicates, but it also excludes real backfilled revenue. 1686 is a pre-backfill snapshot, not a correct figure.

## 2. Fix

**Fix the data.** Remove the re-inserted rows from `seeds/raw_payments.csv`, keeping the lowest `id` for each duplicate group. Then fix the loader so it upserts on the source payment key instead of appending.

Be careful with a blanket dedupe on `(order_id, payment_method, amount)`. Two identical split payments on one order can be legitimate, so check the ids returned by the query above before deleting. If you can't fix it at the source right away, a guard in `stg_payments.sql` is a stopgap:

```sql
renamed as (

    select
        id as payment_id,
        order_id,
        payment_method,

        -- `amount` is currently stored in cents, so we convert it to dollars
        amount / 100 as amount

    from source

    -- the loader re-sent existing payments under new ids; keep the original
    qualify row_number() over (partition by order_id, payment_method, amount order by id) = 1

)
```

**Rebuild the incremental model**, since it will never correct itself:

```bash
dbt build --full-refresh -s daily_revenue+
```

**Make `daily_revenue` robust to backfills.** Ideally, rebuild the dates touched by recently loaded rows. That needs a `_loaded_at` column on the raw tables:

```sql
{% if is_incremental() %}
where order_date in (
    select distinct order_date from {{ ref('stg_orders') }}
    where _loaded_at > (select max(_built_at) from {{ this }})
)
{% endif %}
```

If you don't have a load timestamp, schedule a periodic `--full-refresh`. Also add `--full-refresh` to any backfill runbook.

After the fix, `sum(orders.amount)`, `sum(daily_revenue.revenue)` and `sum(region_revenue.revenue)` should all equal 1737.00. Order 1 should show 10.00.

## 3. Tests that would have caught it

**Duplicate payment content** (add to `models/staging/schema.yml`, requires dbt_utils). Use `warn` because identical split payments can legitimately occur:

```yaml
  - name: stg_payments
    data_tests:
      - dbt_utils.unique_combination_of_columns:
          arguments:
            combination_of_columns: ['order_id', 'payment_method', 'amount']
          config:
            severity: warn
```

**Reconciliation across models.** This catches the stale incremental model. Save as `tests/assert_revenue_reconciles.sql`:

```sql
with totals as (
    select
        (select sum(amount) from {{ ref('stg_payments') }}) as payments,
        (select sum(amount) from {{ ref('orders') }}) as orders,
        (select sum(revenue) from {{ ref('daily_revenue') }}) as daily,
        (select sum(revenue) from {{ ref('region_revenue') }}) as regions
)
select * from totals
where abs(payments - orders) > 0.005
   or abs(orders - daily) > 0.005
   or abs(orders - regions) > 0.005
```

**Reconciliation to the source of truth.** Load the processor settlement totals per period as a seed or source. Then add a singular test that fails when `sum(stg_payments.amount)` for the period differs from the settled amount. That check would have flagged the 180.00 overstatement on the first run after the backfill.
