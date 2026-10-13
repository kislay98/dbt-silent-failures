## 1. Root cause

`models/staging/stg_payments.sql` divides every payment amount by 100:

```sql
-- `amount` is currently stored in cents, so we convert it to dollars
amount / 100 as amount
```

That is only true for `credit_card`, `coupon` and `bank_transfer`. The 12 `gift_card` rows in `seeds/raw_payments.csv` are already in whole dollars, so they get divided by 100 a second time.

The gift card rows (payment id → order, amount):

| payment id | order_id | raw | staged | should be |
|---|---|---|---|---|
| 9 | 9 | 23 | 0.23 | 23.00 |
| 22 | 19 | 6 | 0.06 | 6.00 |
| 26 | 23 | 23 | 0.23 | 23.00 |
| 48 | 43 | 18 | 0.18 | 18.00 |
| 49 | 44 | 11 | 0.11 | 11.00 |
| 67 | 58 | 6 | 0.06 | 6.00 |
| 68 | 59 | 28 | 0.28 | 28.00 |
| 71 | 62 | 14 | 0.14 | 14.00 |
| 92 | 80 | 3 | 0.03 | 3.00 |
| 99 | 87 | 30 | 0.30 | 30.00 |
| 107 | 93 | 26 | 0.26 | 26.00 |
| 110 | 96 | 17 | 0.17 | 17.00 |

The raw values sum to 205, which matches the provider statement. They stage to 2.05.

The numbers reconcile exactly:
- **Gift cards:** 205.00 on the statement against 2.05 in `orders.gift_card_amount`.
- **Order total:** the non-gift-card methods are 411 + 185 + 871 = 1467.00. Adding 205.00 gives 1672.00, which is the settlement total. The 1469.05 in `orders.amount` is 1467.00 + 2.05.
- **Order 9:** 0.23 should be 23.00.
- **Order 87:** shows 26.30 and should be 56.00 (26.00 credit card + 30.00 gift card).
- **Downstream:** `customers.customer_lifetime_value` is understated for the customers with gift card orders, such as 11, 22, 46, 53 and 66.

All 28 checks passed because none of them test values. They only check uniqueness, nulls, accepted values and relationships.

## 2. Fix

In `stg_payments.sql`, convert cents only for the methods that are stored in cents:

```sql
-- gift_card is already in dollars; the other methods are in cents
case when payment_method = 'gift_card' then amount else amount / 100 end as amount
```

Then run `dbt build`. The orders table is rebuilt, and `customers` follows from it.

If the upstream source can send gift cards in cents too, fix the unit at the source instead. The rows above are the evidence that it can't be assumed.

## 3. Test

Add a reconciliation test against the statements finance already has. Put the totals in a seed (`seeds/provider_statement.csv`) with the columns `payment_method,expected_total`. Use these rows: `gift_card,205.00` and `settlement,1672.00`.

`tests/assert_orders_reconcile_to_provider_statements.sql`:

```sql
with actual as (
    select 'gift_card' as k, sum(gift_card_amount) as v from {{ ref('orders') }}
    union all
    select 'settlement', sum(amount) from {{ ref('orders') }}
)
select a.k, a.v, s.expected_total
from actual a
join {{ ref('provider_statement') }} s on s.payment_method = a.k
where abs(a.v - s.expected_total) > 0.005
```

Before the fix this returns both rows (2.05 against 205.00, and 1469.05 against 1672.00). After the fix it returns none.

A cheaper guard is a singular test pinning order 9's `gift_card_amount` to 23.00. It catches this bug but not other unit mix-ups.

I didn't run any of this, because I only had the files pasted here. I also didn't check whether the real gift card source is always in dollars. The `customers` description for `total_order_amount` doesn't match the actual column `customer_lifetime_value`, which is a separate docs bug.
