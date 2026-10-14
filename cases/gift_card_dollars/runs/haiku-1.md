claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 32 s | USD 0.006 | 2026-10-22

## 1. Root cause

**`jaffle_shop/models/staging/stg_payments.sql` divides every payment by 100, but the gift card rows in `jaffle_shop/seeds/raw_payments.csv` are already in dollars.**

The staging model assumes `amount` is in cents for all rows:

```sql
amount / 100 as amount
```

The seed has 12 `gift_card` rows whose values are dollar-scale (3 to 30), not cents-scale like every other method (e.g. `1000` for order 1). Dividing them by 100 shrinks each gift card by 100x.

The 12 affected rows, all with `payment_method = gift_card`:

| raw_payments `id` | `order_id` | raw `amount` | staged `amount` |
|---|---|---|---|
| 9 | 9 | 23 | 0.23 |
| 22 | 19 | 6 | 0.06 |
| 26 | 23 | 23 | 0.23 |
| 48 | 43 | 18 | 0.18 |
| 49 | 44 | 11 | 0.11 |
| 67 | 58 | 6 | 0.06 |
| 68 | 59 | 28 | 0.28 |
| 71 | 62 | 14 | 0.14 |
| 92 | 80 | 3 | 0.03 |
| 99 | 87 | 30 | 0.30 |
| 107 | 93 | 26 | 0.26 |
| 110 | 96 | 17 | 0.17 |

The arithmetic confirms it:

- Raw gift card values sum to 205, which matches the provider statement of 205.00.
- Staged gift card values sum to 2.05, which is what `sum(gift_card_amount)` shows.
- The processor total of 1672.00 equals the current `sum(orders.amount)` of 1469.05 plus the 202.95 gift card shortfall (205.00 − 2.05).
- Order 9 has a 23 gift card row, which is staged as 0.23. Finance's 23.00 redemption matches the raw value.

This error flows through everything downstream. `orders.gift_card_amount` and `orders.amount` are wrong, and so is `customers.customer_lifetime_value`. For example, order 87 shows 26.3 where it should be 56.00, and order 58 shows 18.06 where it should be 24.00.

The existing tests didn't catch it because they are all structural (`unique`, `not_null`, `accepted_values`, `relationships`). The `orders` model derives `amount` from the same staged values, so the internal totals agree with each other. The error is only visible when you compare against an external control total.

## 2. Fix

The raw seed should follow the file's documented convention (cents). Multiply the 12 gift card amounts by 100 in `seeds/raw_payments.csv`:

```
9,9,gift_card,2300
22,19,gift_card,600
26,23,gift_card,2300
48,43,gift_card,1800
49,44,gift_card,1100
67,58,gift_card,600
68,59,gift_card,2800
71,62,gift_card,1400
92,80,gift_card,300
99,87,gift_card,3000
107,93,gift_card,2600
110,96,gift_card,1700
```

The new gift card total is 20500 cents, which is 205.00 after the `/100` in staging. Every other method is unchanged. `sum(amount)` should become 1672.00 and `sum(gift_card_amount)` should become 205.00.

Before you merge, confirm with whoever produces the payments extract that gift card rows are dollars. The provider statement and the processor total both point that way, but the fix only holds if the extract really is inconsistent. The long-term fix is to have the upstream feed emit one unit for every method, so no per-method special case is needed in staging.

I haven't edited any files. I can apply the seed change and rerun the build if you want.

## 3. A test that would have caught it

Add a control-total seed from the provider and processor statements, then a reconciliation test that compares staged totals against it.

`seeds/control_payment_totals.csv`:

```csv
payment_method,control_total
gift_card,205.00
```

Add the other methods' statement totals to this file as well. I only have the gift card control value, so the other rows are left for you to fill in.

`tests/assert_payment_totals_match_control.sql`:

```sql
-- Returns a row (= test failure) when a payment method's staged total
-- drifts from the independent provider statement.
with modelled as (

    select
        payment_method,
        round(sum(amount), 2) as modelled_total
    from {{ ref('stg_payments') }}
    group by payment_method

)

select
    c.payment_method,
    c.control_total,
    m.modelled_total
from {{ ref('control_payment_totals') }} as c
left join modelled as m
    on c.payment_method = m.payment_method
where m.modelled_total is null
   or round(m.modelled_total, 2) <> round(c.control_total, 2)
```

With the bug present, this test fails with `gift_card | 205.00 | 2.05`. Unlike the existing structural tests, it compares against a value that doesn't come from the same pipeline, so a unit error in the pipeline can't cancel out.
