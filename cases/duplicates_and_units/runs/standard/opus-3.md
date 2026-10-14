claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 60 s | USD 0.231 | 2026-10-14 | project ad0b16b | prompt 922df6405b75

# Diagnosis: two problems in `raw_payments`

`dbt build` passes because no test checks amounts against anything external. The SQL in the models is fine. The problems are in the `raw_payments` data that the ingestion job loads, and both surface through `models/staging/stg_payments.sql`.

I haven't run anything against your warehouse. The two causes below are inferred from the numbers you gave, which reconcile exactly. The diagnostic queries in step 2 will confirm them.

## 1. Root cause

**A. Gift card rows are loaded in dollars, not cents.**
`stg_payments.sql` divides every row by 100 (`amount / 100 as amount`). The gift card rows already arrive in dollars, so they get divided twice:
- In `stg_payments`, gift card amounts look like `0.28`, `0.23` and `0.06`. Every other method has whole-dollar values like `19.0` and `15.0`.
- The gift card column in `orders` sums to 2.19. That means the raw gift card amounts sum to about 219 dollars, close to the provider's 205.
- Fees don't show it, because the gift card `fee_rate` is 0.

**B. 12 card and bank payments were loaded twice, each copy with a new `id`.**
- `raw_payments` has 125 rows. The jaffle_shop source has 113 payments for these 99 orders.
- `unique_stg_payments_payment_id` still passes because each duplicate has its own `id`. The repeated payment shows up in `order_id`, `payment_method` and `amount`.
- Order 1 was charged 10.00 once and shows 20.00, which is one such duplicate.

The excess matches the statements exactly:

| method | model | statement | difference |
|---|---|---|---|
| credit_card | 980.00 | 871.00 | +109 (duplicates) |
| bank_transfer | 468.00 | 411.00 | +57 (duplicates) |
| gift_card | 2.19 | 205.00 | 219 raw dollars ÷ 100, which includes about 14 duplicated |
| coupon | 185.00 | 185.00 | 0 |

Check: −109 − 57 + (205 − 2.19) = 36.81, and 1672.00 − 1635.19 = 36.81.

## 2. Fix

**First, confirm both causes:**

```sql
-- B: duplicated payments
select order_id, payment_method, amount, count(*) as copies, list(id order by id) as ids
from raw_payments
group by all
having count(*) > 1
order by order_id;

-- A: unit drift by method
select payment_method, count(*), min(amount), median(amount), max(amount), sum(amount)
from raw_payments
group by 1;
```

**The proper fix is in the ingestion job.** Delete the duplicate rows and load gift cards in cents.

**Until that lands, guard the data in `models/staging/stg_payments.sql`:**

```sql
with source as (

    {#-
    Normally we would select from the table here, but we are using seeds to load
    our data in this project
    #}
    select * from {{ ref('raw_payments') }}

),

deduped as (

    -- the ingestion job re-delivered some payments under new ids; keep the first copy
    select *
    from source
    qualify row_number() over (partition by order_id, payment_method, amount order by id) = 1

),

renamed as (

    select
        id as payment_id,
        order_id,
        payment_method,

        -- `amount` is stored in cents, except gift cards which the provider feed sends in dollars
        case
            when payment_method = 'gift_card' then amount
            else amount / 100
        end as amount

    from deduped

)

select * from renamed
```

Two things to watch with this guard:
- **The dedup key is a heuristic.** It would also collapse a real second payment of the same amount and method on the same order. Check the results against the statements: credit card 871, bank transfer 411, gift card 205 and coupon 185 should add up to exactly 1672.00. If they don't, use the duplicate list from the first query and delete the specific `id`s instead.
- **Run `dbt build --full-refresh -s daily_revenue` after the fix.** Normal runs only rebuild the last 3 days, so the older dates would keep the wrong totals. `daily_revenue` already disagrees with `orders` by month (January 496.00 vs 518.52), so it needs a full rebuild regardless.

## 3. Tests that would have caught it

Add these in `models/staging/schema.yml` under `stg_payments`. The first needs the `dbt_utils` package.

```yaml
  - name: stg_payments
    data_tests:
      # a payment re-delivered under a new id is still a duplicate
      - dbt_utils.unique_combination_of_columns:
          arguments:
            combination_of_columns: ['order_id', 'payment_method', 'amount']
    columns:
      ...
      - name: amount
        tests:
          # sub-dollar payments mean a cents/dollars mix-up
          - dbt_utils.accepted_range:
              arguments:
                min_value: 1
```

The test that would have caught both causes, and any future feed drift, is a singular test that reconciles against the settlement. Save this as `tests/assert_payments_match_settlement.sql`. It assumes you load the processor and provider statement totals as a seed or source, called `settlement` here:

```sql
select s.payment_method, s.amount as settled, coalesce(p.amount, 0) as modelled
from {{ ref('settlement') }} s
left join (
    select payment_method, sum(amount) as amount
    from {{ ref('stg_payments') }}
    group by 1
) p using (payment_method)
where abs(s.amount - coalesce(p.amount, 0)) > 0.01
```

One unrelated issue: `models/schema.yml` documents a `customers.total_order_amount` column, but the model outputs `customer_lifetime_value`.
