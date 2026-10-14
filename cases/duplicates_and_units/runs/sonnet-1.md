claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 35 s | USD 0.088 | 2026-10-14 | project f769fdf+wk6ext | prompt 3077d0cba2a2

## 1. Root cause

Two independent defects in the payments data. Both come in through `raw_payments` and flow through `models/staging/stg_payments.sql`. No test catches either one.

**a) Replayed payment rows (inflates credit card and bank transfer).**
`raw_payments` contains rows with ids 114 and up (12 extra rows: 125 payments where the original jaffle data has 113). Each one is a copy of an earlier payment under a new id:

| order | original | replay |
|---|---|---|
| 1 | id 1, 10.00 | id 114, 10.00 |
| 10 | id 11, 26.00 | id 115, 26.00 |
| 18 | id 21, 8.00 | id 116, 8.00 |
| 62 | id 71, 0.14 | id 121, 0.14 |

That is why order 1 shows 20.00 for a 10.00 charge. `orders.sql` just sums, so the duplicates flow into `amount` and the per-method columns. The `unique(payment_id)` test passes because the replay got fresh ids.

**b) Gift card amounts are in dollars, not cents (deflates gift card).**
`stg_payments.sql` applies `amount / 100` to every method. The comment says "`amount` is stored in cents", but the gift card rows are evidently whole dollars. Order 9 shows 0.23 instead of 23.00. The statement reconciles exactly:
- 2.19 × 100 = 219, minus the replayed 14 on order 62 = **205**, matching the provider.

The other methods reconcile the same way:
- credit card: 980 − 109 duplicated = **871**
- bank transfer: 468 − 57 duplicated = **411**
- coupon: 185, unaffected
- total: 871 + 411 + 205 + 185 = **1672.00**

I only see 4 of the 12 replays in the sample. The 109 and 57 figures are inferred from the statements, not read off the rows.

## 2. Fix

Fix the ingestion job at the source: stop replaying rows and settle the gift card unit with the provider. Until then, patch staging:

```sql
-- models/staging/stg_payments.sql
with source as (

    {#-
    Normally we would select from the table here, but we are using seeds to load
    our data in this project
    #}
    select * from {{ ref('raw_payments') }}

),

deduped as (

    -- the ingestion job replays payments under a new id; keep the first one
    select
        *,
        row_number() over (
            partition by order_id, payment_method, amount
            order by id
        ) as replay_rank
    from source

),

renamed as (

    select
        id as payment_id,
        order_id,
        payment_method,

        -- `amount` is in cents, except gift_card, which the provider feed sends in whole dollars
        case
            when payment_method = 'gift_card' then amount
            else amount / 100
        end as amount

    from deduped
    where replay_rank = 1

)

select * from renamed
```

Caveats:
- This dedup also collapses a legitimately repeated identical payment (same order, method, amount). Before relying on it, check that ids 114–125 are the only rows it removes: `select count(*) from raw_payments where id < 114` should leave 113 rows, and none of them should be dropped.
- `daily_revenue` is incremental and only rebuilds the last 3 days, so it keeps the old inflated history. Run `dbt build --full-refresh` once after the fix.
- `region_revenue` joins `raw_customer_regions` (which has an `updated_at` column) and `orders.sql` joins `raw_payment_methods`. Neither is tested for uniqueness on its join key. If either has multiple rows per key, it will fan out revenue and fees. I haven't seen their data, so check them.

## 3. Tests

**Replay detection on the raw table.** `tests/assert_raw_payments_no_replays.sql`:

```sql
select order_id, payment_method, amount, count(*) as n
from {{ ref('raw_payments') }}
group by 1, 2, 3
having count(*) > 1
```

Fails today on orders 1, 10, 18 and 62, and so on. It will also flag legitimate repeats, which then need an explicit allowlist.

**Reconciliation against the processor statements.** This is the test that catches the gift card unit error, which no row-level test can see. Add a seed `seeds/processor_settlement.csv` with columns `payment_method,expected_total`. Fill it from the statements: 871.00, 411.00, 205.00, 185.00. Then add `tests/assert_payments_match_settlement.sql`:

```sql
with actual as (
    select payment_method, round(sum(amount), 2) as actual_total
    from {{ ref('stg_payments') }}
    group by 1
)

select s.payment_method, s.expected_total, a.actual_total
from {{ ref('processor_settlement') }} s
left join actual a using (payment_method)
where a.actual_total is null
   or abs(a.actual_total - s.expected_total) > 0.005
```

Also add uniqueness tests on the join keys of the two lookup seeds (`raw_customer_regions.customer_id`, `raw_payment_methods.payment_method`), which are missing from `schema.yml`.

I did not run any of this, since I only have the pasted files and output.
