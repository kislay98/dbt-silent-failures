claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 41 s | USD 0.112 | 2026-10-14 | project ad0b16b | prompt 922df6405b75

## 1. Root cause

Two defects sit in the raw payments feed (`seeds/raw_payments.csv`, loaded as `raw_payments`) and in the way `models/staging/stg_payments.sql` handles it. I can't see the seed data, so I worked this out from the arithmetic and the samples. The three per-method gaps add up to the total gap:

| method | ours | statement | gap |
|---|---|---|---|
| credit card | 980.00 | 871.00 | +109.00 |
| bank transfer | 468.00 | 411.00 | +57.00 |
| gift card | 2.19 | 205.00 | −202.81 |
| **total** | 1635.19 | 1672.00 | **−36.81** |

The gaps sum to −36.81, so they explain everything.

**a) Duplicate payment rows (credit card and bank transfer overstated).**
- `raw_payments` has 125 rows, but there are only 99 orders, and the usual jaffle_shop seed has 113.
- The extra 12 rows look like re-ingested copies of existing payments (same `order_id`, `payment_method` and `amount`) under new `id`s.
- This is why the `unique` and `not_null` tests on `payment_id` pass: the ids are distinct.
- It also explains order 1: one 10.00 charge is counted twice, so `amount` shows 20.00.
- The duplicates account for the +109.00 (credit card) and +57.00 (bank transfer), and for 14.00 of gift card.

**b) Gift card amounts are in dollars, not cents.**
- `stg_payments` divides every method by 100, but the gift card rows are whole dollars (staged values such as 0.28, 0.23 and 0.06 come from raw 28, 23 and 6). The other methods are all multiples of 100.
- Gift cards are therefore understated 100×.
- Correcting the unit gives 219.00. Removing the 14.00 of duplicates gives 205.00, which matches the provider's statement.
- Corrected total: 1635.19 + 216.81 − 180.00 = 1672.00, which matches the processor settlement.

**Related problem in `daily_revenue.sql`.**
- Its incremental window only rebuilds the last 3 days, so older days keep the values from earlier runs. For example, January shows 496.0 against 518.52 in `orders`.
- It will stay wrong after the fix until it is fully refreshed.

## 2. Fix

First confirm the duplicates (I expect ids above 113):
```sql
select order_id, payment_method, amount, count(*), min(id), max(id)
from raw_payments group by 1,2,3 having count(*) > 1;
```

Then change `stg_payments.sql`:
```sql
renamed as (
    select
        id as payment_id,
        order_id,
        payment_method,
        -- card, bank and coupon feeds are in cents; the gift card feed is already in dollars
        case when payment_method = 'gift_card' then amount
             else amount / 100 end as amount,
        row_number() over (
            partition by order_id, payment_method, amount
            order by id
        ) as dup_rank
    from source
)
select payment_id, order_id, payment_method, amount
from renamed
where dup_rank = 1
```

- **Check before keeping the dedupe.** If an order can legitimately have two identical payments (a split of 2 × 5.00 on the same method), this key would drop real money. In that case remove the duplicate ids at the source instead and fix the ingestion job. Either way, that job needs fixing so it stops producing the duplicates.
- **Gift card unit.** The cleaner fix is to get the gift card feed delivered in cents. If you do that, drop the `case`.
- **Rebuild.** Run `dbt build --full-refresh -s daily_revenue+ orders+` to clear the stale days.

## 3. Tests that would have caught it

**Duplicate payments**, in `staging/schema.yml` on `stg_payments`. This tolerates the split-payment case by only warning:
```yaml
data_tests:
  - dbt_utils.unique_combination_of_columns:
      arguments:
        combination_of_columns: [order_id, payment_method, amount]
      config: {severity: warn}
```

**Unit check**, as a singular test `tests/assert_payment_amounts_in_cents.sql`. It runs on the raw table, so it needs a `source()` or `ref` to `raw_payments`:
```sql
select * from {{ ref('raw_payments') }}
where mod(amount, 100) != 0   -- flags the gift card dollar rows
```

**Reconciliation**, as `tests/assert_revenue_reconciles.sql`. It also catches the stale `daily_revenue`:
```sql
with o as (select round(sum(amount),2) a from {{ ref('orders') }}),
     d as (select round(sum(revenue),2) a from {{ ref('daily_revenue') }}),
     r as (select round(sum(revenue),2) a from {{ ref('region_revenue') }}),
     s as (select round(sum(amount),2) a from {{ ref('raw_payments') }})  -- adjust units
select * from o, d, r
where o.a != d.a or o.a != r.a
```

For finance's numbers, load the processor and provider statements as a seed (for example `expected_settlement(payment_method, amount)`) and add a singular test that compares `sum(<method>_amount)` from `orders` to it. That is the test that would have flagged all four methods at once.
