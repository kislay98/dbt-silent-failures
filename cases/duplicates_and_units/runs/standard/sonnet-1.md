claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 32 s | USD 0.104 | 2026-10-14 | project ad0b16b | prompt 922df6405b75

## 1. Root cause

Two separate defects in the raw payments data. `stg_payments.sql` handles neither, and no test covers them.

**a) `raw_payments` (seed) has duplicate payment rows.**
- 125 rows against roughly 113 real payments. The duplicates are re-loaded copies of the same `(order_id, payment_method, amount)` with a new `id`.
- That is why `unique_stg_payments_payment_id` passes while order 1 shows 20.00 for a 10.00 charge.
- The inflation matches the finance gaps: credit card is over by 109.00 (980 vs 871) and bank transfer by 57.00 (468 vs 411).

**b) `raw_payments.amount` has mixed units.**
- `stg_payments.sql` divides every row by 100 on the assumption that the column is in cents. That holds for credit_card, coupon and bank_transfer (e.g. `2000` is 20.00).
- Gift card amounts arrive in whole dollars. Order 9 has a gift card payment of `23`, which becomes 0.23.
- This is why gift cards total 2.19 against 205.00.
- The raw gift card total is 219. Removing 14 of duplicated gift card rows leaves 205.00, which matches the provider.

Check: 871 + 411 + 205 + 185 coupons = 1672.00, the settlement figure. The wrong output is 980 + 468 + 2.19 + 185 = 1635.19.

I can't see the seed, so the 14 of duplicated gift card rows is inferred from the arithmetic. Verify with the query below.

**c) Secondary: `daily_revenue` is stale.**
- It is incremental and only rebuilds the last 3 days, so older days still hold values from earlier bad builds.
- January is 496.00 against 518.52 in `orders`, and March is 622 against 584.95.
- It needs a `--full-refresh` after the fix.

## 2. Fix

First, confirm the duplicates are exact repeats and not legitimate split payments:

```sql
select order_id, payment_method, amount, count(*) as n, min(id) as keep_id, list(id) as ids
from raw_payments
group by 1, 2, 3
having count(*) > 1
order by order_id
```

If that returns about 12 rows and includes order 1, fix the ingestion job to stop double-loading. Then fix `models/staging/stg_payments.sql` so the data is correct in the meantime:

```sql
with source as (

    {#-
    Normally we would select from the table here, but we are using seeds to load
    our data in this project
    #}
    select * from {{ ref('raw_payments') }}

),

deduplicated as (

    -- the ingestion job has loaded some payments twice under a new id
    select
        *,
        row_number() over (
            partition by order_id, payment_method, amount
            order by id
        ) as row_num

    from source

),

renamed as (

    select
        id as payment_id,
        order_id,
        payment_method,

        -- `amount` is in cents, except gift_card, which the provider sends in whole dollars
        case
            when payment_method = 'gift_card' then amount
            else amount / 100
        end as amount

    from deduplicated
    where row_num = 1

)

select * from renamed
```

If any order legitimately has two identical payments, partition by a source transaction ID instead, or remove only the known-bad ids.

Then rebuild everything: `dbt build --full-refresh`. Check that totals are 1672.00 overall, 871 credit card, 411 bank transfer, 205 gift card and 185 coupon.

## 3. Tests that would have caught it

The existing tests only check nulls, uniqueness of surrogate IDs and accepted values. None check content against an outside source.

**Reconciliation against the processor and provider statements.** Load their totals as a seed `seeds/processor_statements.csv` with columns `payment_method, statement_amount`, then add `tests/assert_orders_reconcile_to_statements.sql`:

```sql
with ours as (
    select 'credit_card' as payment_method, sum(credit_card_amount) as amount from {{ ref('orders') }}
    union all select 'coupon', sum(coupon_amount) from {{ ref('orders') }}
    union all select 'bank_transfer', sum(bank_transfer_amount) from {{ ref('orders') }}
    union all select 'gift_card', sum(gift_card_amount) from {{ ref('orders') }}
)

select
    ours.payment_method,
    ours.amount,
    statements.statement_amount
from ours
join {{ ref('processor_statements') }} as statements
    on ours.payment_method = statements.payment_method
where abs(ours.amount - statements.statement_amount) > 0.01
```

This fails on all three wrong methods today.

**Duplicate payments**, `tests/assert_no_duplicate_payments.sql`:

```sql
select order_id, payment_method, amount, count(*) as n
from {{ ref('stg_payments') }}
group by 1, 2, 3
having count(*) > 1
```

Before the fix this returns the order 1 duplicate and the others. A unit test would also help: add a `unit_tests:` entry on `stg_payments` with one `gift_card` row of `23` and one `credit_card` row of `2300`, expecting 23.0 for both.
