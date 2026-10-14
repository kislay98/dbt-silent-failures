claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 64 s | USD 0.012 | 2026-10-14 | project ad0b16b | prompt e23be30fc7e7

## 1. Root cause

**`jaffle_shop/models/staging/stg_payments.sql:19`** divides every payment amount by 100:

```sql
amount / 100 as amount
```

The comment above it says "`amount` is currently stored in cents," but that is only true for credit cards, coupons, and bank transfers. The **gift card rows in `seeds/raw_payments.csv` are whole dollars**. The provider's statement confirms it: the 12 gift card amounts in the seed sum to exactly 205, which matches the provider's 205.00 total.

| raw_payments id | order | method | raw `amount` | current `stg_payments.amount` | correct |
|---|---|---|---|---|---|
| 9 | 9 | gift_card | 23 | 0.23 | 23.00 |
| 22 | 19 | gift_card | 6 | 0.06 | 6.00 |
| 26 | 23 | gift_card | 23 | 0.23 | 23.00 |
| 48 / 49 | 43 / 44 | gift_card | 18 / 11 | 0.18 / 0.11 | 18.00 / 11.00 |
| 67 | 58 | gift_card | 6 | 0.06 | 6.00 |
| 68 | 59 | gift_card | 28 | 0.28 | 28.00 |
| 71 | 62 | gift_card | 14 | 0.14 | 14.00 |
| 92 | 80 | gift_card | 3 | 0.03 | 3.00 |
| 99 | 87 | gift_card | 30 | 0.30 | 30.00 |
| 107 | 93 | gift_card | 26 | 0.26 | 26.00 |
| 110 | 96 | gift_card | 17 | 0.17 | 17.00 |

The error propagates to `orders` (`gift_card_amount`, `amount`), `customers` (`customer_lifetime_value`), and `region_revenue`.

The numbers reconcile exactly:
- Gift card under-count: 205.00 − 2.05 = **202.95**
- Settlement gap: 1672.00 − 1469.05 = **202.95**

Order 9 shows 0.23 because its gift card row is stored as 23 dollars and was divided as if it were cents.

**Why every test passed:** the schema tests only check `not_null`, `unique`, `accepted_values`, and `relationships`. Nothing checks magnitude, units, or reconciliation. 0.23 is not null, so `not_null_orders_gift_card_amount` passes.

**A second, related inconsistency:** `daily_revenue` is currently inconsistent with `orders`. Its revenue sums to 1655.17 against 1469.05 in `orders`. The gap is 186.12, which matches the gift card amounts in whole dollars on pre-window dates. For example, 2018-04-03 shows revenue 33.00 = 26 (undivided gift card, order 93) + 7.00 (order 94). Those rows were built before the cents conversion was applied to gift cards, and the incremental model only rebuilds the last three days, so it never corrected them. No test compares `daily_revenue` to `orders`.

## 2. Fix

Change the conversion in `jaffle_shop/models/staging/stg_payments.sql` so only cents-denominated methods are divided:

```sql
        -- `amount` is stored in cents for card, coupon and bank transfer,
        -- but in whole dollars for gift cards (the provider feed is in dollars)
        case
            when payment_method = 'gift_card' then cast(amount as double)
            else amount / 100
        end as amount
```

Then rebuild, including `daily_revenue`, which needs a full refresh to replace its stale pre-window rows:

```
dbt build --full-refresh -s stg_payments+
```

**Expected results after the fix:**
- `sum(orders.amount)`: 1469.05 → **1672.00**
- `sum(orders.gift_card_amount)`: 2.05 → **205.00**
- Order 9: `amount` and `gift_card_amount` 0.23 → **23.00**
- `customers.customer_lifetime_value` and `region_revenue.revenue` also total 1672.00
- `daily_revenue` total: 1655.17 → **1672.00**, matching `orders`
- `fees` do not change, since the gift card `fee_rate` is 0

**Durable option:** add an `amount_unit` column (`cents` / `dollars`) to `seeds/raw_payment_methods.csv` and branch on it in staging, instead of hardcoding `'gift_card'`. Ask the gift card provider or the payments team to confirm the unit, since the feed has no unit metadata.

## 3. Tests that would have caught it

**Primary: a dbt unit test** in `jaffle_shop/models/staging/schema.yml`. It pins the unit conversion for each method, and it fails on the current code because gift card 23 yields 0.23:

```yaml
unit_tests:
  - name: stg_payments_converts_amount_units
    model: stg_payments
    given:
      - input: ref('raw_payments')
        rows:
          - {id: 1, order_id: 1, payment_method: credit_card, amount: 1000}
          - {id: 9, order_id: 9, payment_method: gift_card, amount: 23}
    expect:
      rows:
        - {payment_id: 1, order_id: 1, payment_method: credit_card, amount: 10.0}
        - {payment_id: 9, order_id: 9, payment_method: gift_card, amount: 23.0}
```

**Finance-facing: a reconciliation test against the processor statement.** Add a seed `raw_settlement_totals` (`payment_method`, `settled_amount`) with the statement totals, then a singular test in `tests/`:

```sql
with orders_by_method as (
    select 'credit_card' as payment_method, sum(credit_card_amount) as amount from {{ ref('orders') }}
    union all select 'coupon', sum(coupon_amount) from {{ ref('orders') }}
    union all select 'bank_transfer', sum(bank_transfer_amount) from {{ ref('orders') }}
    union all select 'gift_card', sum(gift_card_amount) from {{ ref('orders') }}
)

select o.payment_method, o.amount, s.settled_amount
from orders_by_method o
join {{ ref('raw_settlement_totals') }} s using (payment_method)
where round(o.amount, 2) <> round(s.settled_amount, 2)
```

This would have failed with `gift_card` at 2.05 against 205.00.

**Also worth adding:** a singular test that `sum(daily_revenue.revenue)` equals `sum(orders.amount)`. It would have caught the stale incremental rows.

**Minor, unrelated:** `customers` in `models/schema.yml` documents a `total_order_amount` column that the model does not produce.
