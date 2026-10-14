claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 57 s | USD 0.210 | 2026-10-14 | project ad0b16b | prompt 922df6405b75

## 1. Root cause

The bug is in the data, not the SQL. `raw_payments`, which the ingestion job loads, has two defects. Neither is caught by `stg_payments.sql` or any test.

**A. Duplicate payment rows.** `raw_payments` has 125 rows. The jaffle_shop payments feed has 113. The 12 extra rows repeat existing payments (same `order_id`, `payment_method` and `amount`) under new `id`s. That's why `unique_stg_payments_payment_id` still passes. `orders.sql` sums every payment per order, so duplicated payments count twice:

- Order 1 shows 20.00 because its single 10.00 credit card payment appears twice.
- The extras add 109.00 to credit card (980 vs 871) and 57.00 to bank transfer (468 vs 411).

**B. Gift card amounts loaded in dollars, not cents.** `stg_payments.sql` divides every `amount` by 100, as its comment says, because the feed is in cents. Gift card rows arrive in dollars, so they get divided a second time:

- Order 9 has `gift_card_amount = 0.23`, which is a $23 gift card.
- Gift card amounts are 0.06, 0.23 and 0.28, while every other method is in whole dollars.
- Raw gift card total is 219 dollars, shown as 2.19. That is 205 real dollars plus 14 dollars of duplicates from (A).

**The numbers reconcile exactly.** Coupons have no duplicates and no unit problem, which is why they match:

| Method | Correct | Bug effect |
|---|---|---|
| Credit card | 871 | + 109 duplicates |
| Bank transfer | 411 | + 57 duplicates |
| Gift card | 205 | + 14 duplicates, then ÷100 |
| Coupon | 185 | none |

- Correct total: 871 + 185 + 411 + 205 = **1672.00** ✓
- Current total: 980 + 185 + 468 + 2.19 = **1635.19** ✓

The same bad payments flow into `customers`, `region_revenue` and `daily_revenue`. `fees` is overstated by the duplicated card and bank rows.

**Confirm before fixing.** I haven't seen the seed or raw data itself, so run these first:

```sql
-- (A) duplicates: expect 12 extra rows, and order 1 in the list
select order_id, payment_method, amount, count(*) n, list(id order by id) ids
from raw_payments group by all having count(*) > 1;

-- (B) units: gift_card should be the only method with amounts not divisible by 100
select payment_method, count(*), min(amount), max(amount), sum(amount),
       count(*) filter (where amount % 100 <> 0) as not_whole_dollars
from raw_payments group by 1;
```

Also check with the payments owner whether a customer can legitimately make two identical payments on one order. If they can, (A) must be fixed at ingestion rather than by deduplicating on those columns.

## 2. Fix

**The proper fix is upstream.** The ingestion job should stop re-emitting payments under new ids and should load gift card amounts in cents. Then reload `raw_payments`.

**Until then, guard in `models/staging/stg_payments.sql`:**

```sql
with source as (

    {#-
    Normally we would select from the table here, but we are using seeds to load
    our data in this project
    #}
    select * from {{ ref('raw_payments') }}

),

deduplicated as (

    -- the feed re-delivers some payments under a new id; keep the original
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

        -- `amount` is stored in cents, except gift cards, which the feed sends in dollars
        case
            when payment_method = 'gift_card' then amount
            else amount / 100
        end as amount

    from deduplicated

)

select * from renamed
```

Once the upstream feed is fixed, delete the `gift_card` branch. Otherwise the amounts will be wrong in the other direction.

**Rebuild `daily_revenue` with a full refresh.** It is incremental and only reprocesses the last 3 days, so a normal run leaves all older dates wrong. Its monthly totals already disagree with `orders` (e.g. Jan 496.00 vs 518.52).

```
dbt build --full-refresh -s stg_payments+
```

**Expected result:** total 1672.00; credit card 871.00; bank transfer 411.00; gift card 205.00; coupon 185.00; order 1 = 10.00.

## 3. Tests that would have caught this

Add these to `models/staging/schema.yml` under `stg_payments`. The first needs `dbt_utils`; you could write it as a singular test instead.

```yaml
  - name: stg_payments
    tests:
      - dbt_utils.unique_combination_of_columns:
          arguments:
            combination_of_columns: ['order_id', 'payment_method', 'amount']
    columns:
      ...
      - name: amount
        tests:
          - not_null
          - dbt_utils.accepted_range:
              arguments:
                min_value: 1   # no real payment is under $1; a 0.23 gift card means double /100
```

**Strongest guard: reconcile against settlement.** Add a singular test, `tests/assert_payments_reconcile_to_settlement.sql`. The expected totals should come from a settlement seed or source table, kept up to date with each statement. They are hard-coded here only to show the idea.

```sql
with expected as (
    select * from (values
        ('credit_card', 871.00), ('bank_transfer', 411.00),
        ('gift_card', 205.00),   ('coupon', 185.00)
    ) t(payment_method, settled_amount)
),
actual as (
    select payment_method, sum(amount) as amount
    from {{ ref('stg_payments') }} group by 1
)
select e.payment_method, e.settled_amount, a.amount
from expected e
left join actual a using (payment_method)
where abs(coalesce(a.amount, 0) - e.settled_amount) > 0.01
```

Any of these would have failed last night's build. The current tests only check ids, nulls and accepted values, so they can't see duplicates with fresh ids or a unit error.

**Unrelated issue I noticed:** `models/schema.yml` documents a `customers.total_order_amount` column, but the model outputs `customer_lifetime_value`.
