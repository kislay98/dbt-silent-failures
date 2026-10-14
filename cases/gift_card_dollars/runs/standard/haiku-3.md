claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 50 s | USD 0.010 | 2026-10-14 | project ad0b16b | prompt e23be30fc7e7

## 1. Root cause

**The `amount` column in `raw_payments.csv` mixes units, and `stg_payments` assumes it is all cents.**

- `models/staging/stg_payments.sql` converts every row with `amount / 100`, and its comment says the amount is stored in cents.
- Non-gift-card rows are in cents. Order 1's credit card is `1000`, which becomes 10.00.
- The 12 `gift_card` rows are already whole dollars. Order 9's gift card is `23`, meaning 23.00, which the provider confirms. `stg_payments` turns it into 0.23.

The 12 affected rows in `seeds/raw_payments.csv` (`id`, line number, value as stored, correct value in cents):

| id | line | order | stored | should be |
|---|---|---|---|---|
| 9 | 10 | 9 | 23 | 2300 |
| 22 | 23 | 19 | 6 | 600 |
| 26 | 27 | 23 | 23 | 2300 |
| 48 | 49 | 43 | 18 | 1800 |
| 49 | 50 | 44 | 11 | 1100 |
| 67 | 68 | 58 | 6 | 600 |
| 68 | 69 | 59 | 28 | 2800 |
| 71 | 72 | 62 | 14 | 1400 |
| 92 | 93 | 80 | 3 | 300 |
| 99 | 100 | 87 | 30 | 3000 |
| 107 | 108 | 93 | 26 | 2600 |
| 110 | 111 | 96 | 17 | 1700 |

Those 12 rows sum to 205, which matches the provider statement, so the raw data is the dollar figure and only the conversion is wrong.

**The 1672.00 settlement total checks out.** Correcting only the gift cards gives 1469.05 − 2.05 + 205.00 = 1672.00.

**Order 9:** payment 9 is the 23 gift card and payment 10 is a 0 bank transfer. The order should be 23.00 and currently shows 0.23.

**Why the tests pass:** they only check `not_null`, `unique`, and `accepted_values`. Nothing compares amounts to an independent source, and the per-method columns sum to `amount` either way.

**Also affected: `daily_revenue`.** Its revenue totals 1655.17 against 1469.05 in `orders`. This is a second problem:
- Jan, Feb and Mar still hold correct values from an earlier build. The incremental model only rebuilds the last three days (from 2018-04-06), so those older rows never picked up the bug.
- The rebuilt window (orders 96–99) carries the wrong values: 0.17 + 14.00 + 10.00 + 24.00 = 48.17. The stale rows contribute the rest, 1607.00, for a total of 1655.17.

Fixing `orders` alone will not fix `daily_revenue`.

## 2. Fix

**Data fix (recommended).** Make the gift card rows cents like the rest of the column. In `seeds/raw_payments.csv`, change the `amount` for the 12 ids above to the values in the last column (×100). The gift card total then becomes 20500 cents, which is 205.00 after the existing `/100`.

If the gift card feed really does emit dollars, fix it in the loader that produces `raw_payments` rather than in the seed, so the next load does not reintroduce the problem.

**Then rebuild the stale table:**

```
dbt seed
dbt build
dbt run -s daily_revenue --full-refresh
```

**Alternative (code-only).** If the seed must stay as is, make the unit explicit in `models/staging/stg_payments.sql`:

```sql
case
    when payment_method = 'gift_card' then amount   -- provider feed is in dollars
    else amount / 100.0
end as amount
```

This works, but the column then has mixed units. The data fix is cleaner.

**Expected results after the fix:** order 9 = 23.00, `sum(orders.gift_card_amount)` = 205.00, `sum(orders.amount)` = 1672.00, and `daily_revenue` = 1672.00 after the full refresh.

I have not run any of this. The numbers above come from the outputs you pasted plus arithmetic on them.

## 3. A test that would have caught it

Test against an independent source, which is what finance did. Add the provider's statement as a seed, then compare:

`seeds/raw_gift_card_statement.csv`:
```csv
redemption_date,redeemed_amount
,
```
Replace the empty row with the statement's actual rows, in dollars.

`tests/assert_gift_card_total_matches_provider.sql`:
```sql
with provider as (
    select sum(redeemed_amount) as total
    from {{ ref('raw_gift_card_statement') }}
),

orders_total as (
    select sum(gift_card_amount) as total
    from {{ ref('orders') }}
)

select provider.total as provider_total, orders_total.total as orders_total
from provider
cross join orders_total
where abs(provider.total - orders_total.total) > 0.005
```

This fails today (205.00 vs 2.05). The same pattern applies to the processor settlement total (1672.00) against `sum(stg_payments.amount)`, which would also have caught the problem.

An order-level check like `amount = credit_card + coupon + bank_transfer + gift_card` would not have caught it, because both sides use the same wrong units.

## Other things I noticed

- `models/schema.yml` documents `total_order_amount` on `customers`, but the model has no such column (it's `customer_lifetime_value`). It isn't tested, so nothing fails, but the docs are wrong.
- `daily_revenue` is incremental with a three-day rebuild window, so any upstream correction to older dates will not reach it without a `--full-refresh`. Worth documenting in the model.
