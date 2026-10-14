claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 48 s | USD 0.224 | 2026-10-14 | project ad0b16b | prompt 1c80550dffb3

# Diagnosis: duplicate payments from the backfill, plus a stale `daily_revenue`

I can't see the seed CSVs, so I worked this out from the totals in your output. Run the confirmation query in section 1 before you edit any data.

## 1. Root cause

There are two separate defects. Together they explain all three numbers.

### A. `seeds/raw_payments.csv`: the backfill re-inserted payments that were already loaded (+180.00)

- **Order 1.** It shows 20.00 but the customer was charged 10.00. Its payment exists twice with different `id`s.
- **The extra rows.** `raw_payments` has 131 rows. The backfilled rows have high ids (114+), and some point at old orders, such as `id 121 → order_id 10`, a January order. A February backfill has no reason to add payments to January or March orders unless they are re-sent copies.
- **Why the tests pass.** `unique_stg_payments_payment_id` passes because each copy has a new `id`. Nothing tests the payment itself for uniqueness, or reconciles totals.
- **How it spreads.** `orders.sql` sums all payments per order, so `orders.amount`, `fees`, `customers.customer_lifetime_value` and `region_revenue` are all inflated. `region_revenue` sums to 1917, the same as `orders`.
- **The size of the error.** 1917.00 − 1737.00 = **180.00** of duplicate payments.

Confirm it with this query:
```sql
select order_id, payment_method, amount,
       list(id order by id) as ids, count(*) as n
from raw_payments
group by all
having count(*) > 1
order by order_id;
-- sum of (n - 1) * amount over these rows should be 18000 cents
```

### B. `models/daily_revenue.sql`: the incremental filter never picks up backfills (−231.00)

The filter is `order_date >= max(order_date) - 3 days`. Last night's run therefore only rebuilt early April. Everything the backfill changed in January to March was skipped:

| month | `orders` | `daily_revenue` | gap |
|---|---|---|---|
| Jan | 570 / 29 orders | 496 / 29 | 74 (duplicate payments on existing orders) |
| Feb | 515 / 33 orders | 415 / 27 | 100 (6 backfilled orders, 100–105, plus duplicates) |
| Mar | 679 / 35 orders | 622 / 35 | 57 |
| Apr | 153 / 8 orders | 153 / 8 | 0 (inside the lookback) |

1917 − 231 = **1686**. The dashboard is stale, not correct. It matches neither `orders` nor the settlement, and any match with the settlement would be a coincidence.

## 2. Fix

**Data.** Delete the duplicate rows found by the query above from `seeds/raw_payments.csv`, keeping the lowest `id` in each group. Then fix the loader so a backfill upserts rather than appends. Check the list before deleting anything: a real split payment with the same method and amount would also show up.

**Model.** A lookback window can't handle backfills of arbitrary age. The table is a few hundred rows, so make it a plain table:

```sql
{{
    config(
        materialized='table'
    )
}}

with orders as (

    select * from {{ ref('orders') }}

),

final as (

    select
        order_date,
        count(*) as orders,
        sum(amount) as revenue

    from orders

    group by order_date

)

select * from final
```

If it has to stay incremental, rebuild it with `dbt build --full-refresh -s daily_revenue` after every backfill.

**Rebuild:**
```
dbt build --full-refresh
```

**Expected result:**
- `sum(orders.amount)` = `sum(daily_revenue.revenue)` = `sum(region_revenue.revenue)` = **1737.00**
- Order 1 = **10.00**

## 3. Tests that would have caught this

**Duplicate payments.** Add this to `models/staging/schema.yml`. It needs `dbt_utils`; a singular test with the `group by ... having` query above works too.

```yaml
  - name: stg_payments
    tests:
      - dbt_utils.unique_combination_of_columns:
          arguments:
            combination_of_columns: ['order_id', 'payment_method', 'amount']
          config:
            severity: warn   # a legit split payment can repeat; investigate on warn
```

**Stale incremental.** Add `tests/assert_daily_revenue_reconciles_to_orders.sql`:
```sql
-- daily_revenue must total exactly what orders totals; fails if an incremental run missed a backfill
select
    (select sum(amount) from {{ ref('orders') }}) as orders_total,
    (select sum(revenue) from {{ ref('daily_revenue') }}) as daily_revenue_total,
    (select count(*) from {{ ref('orders') }}) as orders_count,
    (select sum(orders) from {{ ref('daily_revenue') }}) as daily_revenue_count
where orders_total is distinct from daily_revenue_total
   or orders_count is distinct from daily_revenue_count
```

**Settlement reconciliation (best option).** If finance's per-period settlement totals can be loaded as a seed or source, add a singular test comparing `sum(stg_payments.amount)` per period against them. That catches any over-count, whatever its cause.

**Unrelated doc fix.** `models/schema.yml` documents a `customers.total_order_amount` column that doesn't exist. The model outputs `customer_lifetime_value`, so rename the entry.
