claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 32 s | USD 0.084 | 2026-10-14 | project f769fdf+wk6ext | prompt 3077d0cba2a2

## 1. Root cause

Two defects in `raw_payments` and `stg_payments.sql` explain every number. I worked from the samples you pasted, not the seed files, so the ids and totals below are what the sample shows.

**a) Replayed payment rows with fresh ids (credit_card and bank_transfer are overstated).**
`raw_payments` contains a second copy of some payments, appended with new ids (114, 115, 116, 121, ...). Each copy has the same `order_id`, `payment_method` and `amount` as an earlier row:

| order | original | replay | effect |
|---|---|---|---|
| 1 | payment 1 (10.00) | payment 114 (10.00) | order shows 20.00, customer was charged 10.00 |
| 10 | payment 11 (26.00) | payment 115 (26.00) | |
| 18 | payment 21 (8.00) | payment 116 (8.00) | |
| 62 | payment 71 (0.14) | payment 121 (0.14) | |

The `unique` test on `payment_id` can't catch this because the replays got new ids. The overstatement matches the gaps in your numbers:
- credit_card: 980 − 871 = 109
- bank_transfer: 468 − 411 = 57
- coupon: no replays, which is why it matches.

**b) Gift card amounts are in whole dollars, not cents (`models/staging/stg_payments.sql`).**
The model does `amount / 100` for every method. Gift card rows (0.23, 0.06, 0.14, ...) are really 23, 6 and 14 dollars, so they come out 100 times too small.
- The raw gift card total is 219, and 219 − 14 (the replayed row on order 62) = 205.00, which is exactly the provider's redemption statement.
- Together with (a), this closes the gap: 871 + 185 + 411 + 205 = 1672.00.

## 2. Fix

**Data:** have the ingestion job stop replaying batches, and delete the replayed rows (ids ≥ 114 that duplicate earlier rows) from `raw_payments`. Deduping on `order_id`, `payment_method` and `amount` would also collapse genuine split payments of the same amount, so don't rely on that alone.

**Code**, in `stg_payments.sql`, as a guard plus the unit fix:

```sql
renamed as (

    select
        id as payment_id,
        order_id,
        payment_method,

        -- `amount` is stored in cents, except gift_card, which the provider feed sends in whole dollars
        case
            when payment_method = 'gift_card' then amount * 1.0
            else amount / 100
        end as amount

    from source

    -- the ingestion job replays rows under new ids; keep the first copy
    qualify row_number() over (
        partition by order_id, payment_method, amount
        order by id
    ) = 1

)
```

After this, `daily_revenue` has to be rebuilt in full. It is incremental and only reprocesses the last 3 days, so the old inflated history will stay:

```
dbt build --full-refresh -s daily_revenue+
```

`customers` and `region_revenue` are tables built from `stg_payments`, so a normal build fixes them.

## 3. Test that would have caught it

The existing tests only check shape (unique, not null, accepted values), never amounts. The test below reconciles against the external statements. Load the statement totals as a seed called `processor_statements` with columns `payment_method` and `statement_total`.

```sql
-- tests/assert_payments_reconcile_to_statements.sql
with model as (

    select payment_method, round(sum(amount), 2) as total
    from {{ ref('stg_payments') }}
    group by 1

)

select
    s.payment_method,
    m.total as model_total,
    s.statement_total
from {{ ref('processor_statements') }} s
left join model m using (payment_method)
where m.total is null
   or abs(m.total - s.statement_total) > 0.005
```

This fails on all three affected methods today. For the replays specifically, add this as a `warn`-severity check on `stg_payments`, since same-amount split payments can be legitimate:

```yaml
data_tests:
  - dbt_utils.unique_combination_of_columns:
      arguments:
        combination_of_columns: [order_id, payment_method, amount]
      config:
        severity: warn
```

## Not confirmed

I couldn't verify these from the sample, but they are worth checking:
- **`raw_customer_regions`** has an `updated_at` column but is documented as "one row per customer". If it holds history, `region_revenue` fans out. Add a `unique` test on `customer_id`.
- **`raw_payment_methods`** has no `unique` test on `payment_method`. A duplicate there would fan out the join in `orders.sql` and inflate `amount` and `fees`.
