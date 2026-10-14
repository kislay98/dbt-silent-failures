claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 29 s | USD 0.098 | 2026-10-14 | project ad0b16b | prompt 922df6405b75

**Caveat:** I can't see the seed data, so I derived this from the totals and samples. The queries below confirm it.

## 1. Root cause

Two defects in `seeds/raw_payments.csv`, which `models/staging/stg_payments.sql` turns into wrong totals. Every downstream table (`orders`, `customers`, `daily_revenue`, `region_revenue`) just sums the staged values, so they all agree with each other and are all wrong.

**a) Gift cards are loaded in dollars, but `stg_payments.sql` divides every method by 100.**
- The sample shows gift-card rows as `0.28` after staging, so the raw value is `28`, not `2800`.
- Gift cards come out at 2.19 against 205.00 on the provider statement, about 100x too low.
- The raw sum is ~219. That is $14 over the 205 statement, which is explained by (b).

**b) The ingestion job loaded some payments twice, under new `id`s.**
- Credit cards are over by 109.00, bank transfers by 57.00, and gift cards by about 14.00 once the unit is fixed.
- `unique(payment_id)` passes because the duplicates have fresh ids.
- Order 1 shows 20.00 for a 10.00 charge, so it has two identical 10.00 credit-card rows.
- Coupons match because none were duplicated.
- The numbers reconcile: 109 + 57 − 202.81 = −36.81, which is exactly 1635.19 − 1672.00.

**Secondary:** `daily_revenue` is stale. January shows 496.00 against 518.52 in `orders`. The incremental model only rebuilds the last 3 days, so older days keep whatever an earlier run computed. It needs a `--full-refresh` after the fix.

## 2. Fix

First, confirm the duplicates:

```sql
select order_id, payment_method, amount, count(*) n, list(id) ids
from raw_payments
group by 1,2,3
having count(*) > 1
order by order_id;
```

Remove the duplicate rows at the source by deleting the later ids in the ingestion data. Legitimate split payments of the same amount may exist, so check the list above against the processor statement before deleting.

Then fix the units in `stg_payments.sql`:

```sql
renamed as (

    select
        id as payment_id,
        order_id,
        payment_method,

        -- card, bank and coupon amounts are stored in cents; gift_card is already in dollars
        case
            when payment_method = 'gift_card' then cast(amount as double)
            else amount / 100
        end as amount

    from source

)
```

Better still, have the ingestion job write gift cards in cents so the staging model stays uniform. Then rebuild with `dbt build --full-refresh`. Expected result: total 1672.00, credit card 871.00, bank transfer 411.00, gift card 205.00.

## 3. Tests that would have caught it

None of the existing tests compare against an outside source. A reconciliation test against the processor statements would have caught both defects. Load the statements as a seed, `seeds/processor_settlement.csv` with `payment_method, settled_amount`, and add `tests/assert_payments_match_processor_statement.sql`:

```sql
with ours as (
    select payment_method, round(sum(amount), 2) as amount
    from {{ ref('stg_payments') }}
    group by 1
)
select o.payment_method, o.amount, s.settled_amount
from ours o
join {{ ref('processor_settlement') }} s using (payment_method)
where abs(o.amount - s.settled_amount) > 0.01
```

Add a duplicate guard that doesn't depend on the surrogate id, `tests/assert_no_duplicate_payments.sql`:

```sql
select order_id, payment_method, amount, count(*) as n
from {{ ref('raw_payments') }}
group by 1, 2, 3
having count(*) > 1
```

Configure it with `severity: warn` if split payments of equal amounts are legitimate.

Also add a check that `orders.amount` equals the sum of the four method columns.
