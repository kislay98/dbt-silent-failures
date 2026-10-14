claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 34 s | USD 0.087 | 2026-10-14 | project f769fdf+wk6ext | prompt 3077d0cba2a2

## 1. Root cause

The problem is in the raw payments data and in `models/staging/stg_payments.sql`, which trusts that data. `orders.sql` is fine. There are two separate defects, and they explain every number.

**a) Duplicated payment rows in `raw_payments`.**
Payment ids 114 and up are re-loaded copies of earlier payments. They have new ids but the same order, method and amount:

| order | original | copy |
|---|---|---|
| 1 | id 1, credit_card 10.00 | id 114 |
| 10 | id 11, bank_transfer 26.00 | id 115 |
| 18 | id 21, credit_card 8.00 | id 116 |
| 62 | id 71, gift_card 0.14 | id 121 |

This is why order 1 shows 20.00 for a customer charged 10.00. The copies have fresh primary keys, so `unique_stg_payments_payment_id` passes. The duplicates add 109 to credit card (980 vs 871) and 57 to bank transfer (468 vs 411). Coupons are untouched, so they match.

**b) Gift card amounts are already in whole dollars, but `stg_payments.sql` divides everything by 100.**
The comment says "`amount` is currently stored in cents", which is true for the other methods and false for gift cards. The gift card feed gives 23 for $23.00, and the model turns it into 0.23. That is why the output has `0.23`, `0.06` and `0.28` instead of dollar amounts. After removing the duplicate (0.14 becomes 0.14 cents' worth, i.e. 14), the raw gift card sum is 205, matching the provider's 205.00. The model reports 2.19, which is 219/100 including the duplicate.

I haven't seen the seed rows. The unit finding comes from the reconciliation below, so confirm it with `select sum(amount) from raw_payments where payment_method = 'gift_card'` (expect 219 before dedup, 205 after).

**Reconciliation:** 871 + 185 + 411 + 205 = 1672.00, which equals the processor settlement.

**Why the tests passed:** the tests check structure, not correctness. They cover unique ids, `not_null` on sums that can never be null, and `accepted_values` on method names. Nothing compares totals to an external source, checks for repeated business keys, or checks units.

## 2. Fix

Edit `models/staging/stg_payments.sql`:

```sql
with source as (

    {#-
    Normally we would select from the table here, but we are using seeds to load
    our data in this project
    #}
    select * from {{ ref('raw_payments') }}

),

deduped as (

    -- the ingestion job re-loaded some payments under new ids; keep the first copy
    select *
    from source
    qualify row_number() over (
        partition by order_id, payment_method, amount
        order by id
    ) = 1

),

renamed as (

    select
        id as payment_id,
        order_id,
        payment_method,

        -- `amount` is stored in cents, except gift_card, which the provider feed sends in whole dollars
        case
            when payment_method = 'gift_card' then amount
            else amount / 100
        end as amount

    from deduped

)

select * from renamed
```

Caveats:
- The dedup key assumes an order never has two genuinely identical payments by the same method. Check the rows it drops (all should have id ≥ 114) with an anti-join against the kept ids. If legitimate repeats exist, instead delete the re-loaded batch in the ingestion job, which is the better long-term fix anyway.
- The gift card rule belongs in the ingestion job too. Normalize units at the source if you can.
- `daily_revenue` is incremental and only rebuilds the last three days, so historical rows keep the wrong values. Run `dbt build --full-refresh` once after the change.

## 3. Tests

**Duplicate check** (`tests/assert_no_duplicate_payments.sql`):

```sql
select order_id, payment_method, amount, count(*) as copies
from {{ ref('stg_payments') }}
group by 1, 2, 3
having count(*) > 1
```
Run this against the raw table in a pre-dedup check (or in the ingestion job). Once staging dedups, it only catches regressions.

**Reconciliation to the external statements** (the test that would have caught both defects). Load the finance statement figures as a seed, `seeds/processor_statements.csv` with columns `payment_method,statement_total`, and add `tests/assert_method_totals_match_statements.sql`:

```sql
with model as (
    select 'credit_card' as payment_method, sum(credit_card_amount) as total from {{ ref('orders') }}
    union all select 'coupon', sum(coupon_amount) from {{ ref('orders') }}
    union all select 'bank_transfer', sum(bank_transfer_amount) from {{ ref('orders') }}
    union all select 'gift_card', sum(gift_card_amount) from {{ ref('orders') }}
)

select m.payment_method, m.total, s.statement_total
from model m
join {{ ref('processor_statements') }} s using (payment_method)
where abs(m.total - s.statement_total) > 0.005
```

**Adjacent gaps in the same style** (not part of this incident's numbers, so unverified):
- `raw_payment_methods.payment_method` has no `unique` test. A duplicate row there would fan out `orders.amount`.
- `raw_customer_regions.customer_id` has no `unique` test, and the table carries `updated_at`, which suggests multiple rows per customer. That would inflate `region_revenue`. Add `unique` on both columns, or select the latest row per customer.
