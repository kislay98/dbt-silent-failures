claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 82 s | USD 0.012 | 2026-10-14 | project ad0b16b | prompt 922df6405b75

## Summary

The dbt logic in `orders.sql` and `customers.sql` is correct. The bad numbers come from `stg_payments.sql`, which assumes every raw payment amount is in cents, and from the `raw_payments` rows that the ingestion job loads. I could not query `raw_payments` directly, so the data diagnosis below is inferred from the aggregates and should be confirmed with the queries in section 1.

## 1. Root cause

**Finding A: gift card amounts are 100× too small (high confidence on the pattern).**
- `models/staging/stg_payments.sql` applies `amount / 100` to every row, based on the comment "`amount` is currently stored in cents".
- Gift card rows in `stg_payments` are all under $1 (0.28, 0.23, 0.06, 0.52). Real gift card redemptions are not typically 23¢. The values look like dollar amounts that were divided by 100 a second time. For example, a $28.00 redemption stored as `28` becomes `0.28`.
- Model gift card total is 2.19 and the processor says 205.00. Dividing a dollar-denominated raw total of about 219 by 100 gives roughly 2.19. The remaining ~14 gap against 205 is not explained yet.
- The `raw_payments` sample shows no gift card rows, so I cannot confirm the unit from it.

**Finding B: credit card (+109) and bank transfer (+57) are overstated, probably by duplicate payment rows (medium-high confidence).**
- Order 1 shows 20.00 but was charged 10.00. The `orders` model sums every `stg_payments` row per order, so two rows of 10.00 for order 1 would produce exactly this.
- The duplicates most likely have different `payment_id`s, which is why `unique` on `payment_id` passes. The ingestion job probably re-delivered some payments.
- Coupons match, which fits duplicates occurring only in card and bank rows.
- Check: model total is 1635.19 and settlement is 1672.00. Adding the +109 and +57 overstatements to the −202.81 gift card shortfall gives −36.81, which is exactly the total gap.

**Finding C: `daily_revenue` is stale and does not reconcile to `orders` (separate, but finance will see it).**
- The daily revenue sums to 1669.17 but `orders` totals 1635.19. Counts match (99), so revenue drifts by date. For example, January is −22.52 and March is +37.05.
- `daily_revenue.sql` only rebuilds the last three days on each incremental run. Older dates keep whatever amounts they had when they were first built, so late payments and corrections never reach them.

**Also:** the build log shows "32 data tests" found but only 28 run. The 4 missing tests are the `raw_customer_regions` and `raw_payment_methods` seed tests. Those seeds did not run, so their tests did not run either.

**Confirm with these queries:**

```sql
-- A: gift card unit
select amount, count(*) from raw_payments
where payment_method = 'gift_card' group by 1 order by 1;

-- B: duplicate candidates
select order_id, payment_method, amount, count(*) as n, list(id) as ids
from raw_payments
group by all having count(*) > 1
order by n desc;

-- C: order 1
select * from raw_payments where order_id = 1;

-- C: drift between daily_revenue and orders
select o.order_date, d.revenue as daily_revenue, sum(o.amount) as orders_revenue
from orders o
join daily_revenue d using (order_date)
group by o.order_date, d.revenue
having abs(d.revenue - sum(o.amount)) > 0.01;
```

## 2. Fix

**Data (preferred):** have the ingestion job emit one unit for `raw_payments.amount`, then drop the `/ 100` special case. If it is not fixed upstream yet, normalise in staging, but only after confirming the gift card rows are dollars:

```sql
-- models/staging/stg_payments.sql
renamed as (

    select
        id as payment_id,
        order_id,
        payment_method,

        -- card, bank and coupon feeds are cents; the gift card feed is dollars
        case when payment_method = 'gift_card' then amount else amount / 100.0 end as amount

    from source

)
```

**Duplicates:** do not dedupe on `(order_id, payment_method, amount)` alone, because a customer can legitimately make two identical payments. Dedupe only on the processor's transaction ID if the feed has one. If it does not, the ingestion job needs to dedupe on re-delivery.

**`daily_revenue`:** the table has about 69 rows, so a full rebuild is cheap. Change it to a table:

```sql
-- models/daily_revenue.sql
{{ config(materialized='table') }}
```

Then rebuild once with `dbt build -s stg_payments+ --full-refresh` after the upstream fix.

## 3. Tests that would have caught this

None of the existing tests check amounts. `unique` on `payment_id` is blind to duplicates with new IDs, and `not_null` and `accepted_values` only check shape. The test that would have caught this reconciles payments by method against the processor statement. Load the statement as a seed, `seeds/processor_settlement.csv` with columns `payment_method, amount`, then add:

```sql
-- tests/assert_payments_reconcile_to_settlement.sql
with ledger as (

    select payment_method, round(sum(amount), 2) as ledger_amount
    from {{ ref('stg_payments') }}
    group by payment_method

),

settlement as (

    select payment_method, amount as settlement_amount
    from {{ ref('processor_settlement') }}

)

select
    coalesce(ledger.payment_method, settlement.payment_method) as payment_method,
    ledger.ledger_amount,
    settlement.settlement_amount

from ledger
full join settlement using (payment_method)
where abs(coalesce(ledger_amount, 0) - coalesce(settlement_amount, 0)) > 0.01
```

Two smaller tests to add alongside it:

- **Duplicate detection (warn):** `dbt_utils.unique_combination_of_columns` on `stg_payments(order_id, payment_method, amount)` with `severity: warn`. It surfaces duplicates without blocking legitimate split payments.
- **Daily reconciliation:** a singular test that fails when `daily_revenue.revenue` differs from `sum(orders.amount)` for any date. Run it after the `daily_revenue` change to confirm it passes.

Also make sure the 4 seed tests actually run in the nightly build. Right now the build skips them without an error.
