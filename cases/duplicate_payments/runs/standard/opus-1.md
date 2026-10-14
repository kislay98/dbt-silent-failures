claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 37 s | USD 0.254 | 2026-10-14 | project ad0b16b | prompt b041d9ee24db

## 1. Root cause: duplicated payment rows in `seeds/raw_payments.csv`

Rows **`id` 114–125** in `jaffle_shop/seeds/raw_payments.csv` duplicate earlier payments. Each has the same `order_id`, `payment_method`, and `amount` as an earlier row, but a new `id`:

| dup id | original id | order_id | method | amount (¢) |
|---|---|---|---|---|
| 114 | 1 | 1 | credit_card | 1000 |
| 115 | 11 | 10 | bank_transfer | 2600 |
| 116 | 21 | 18 | credit_card | 800 |
| 117 | 31 | 26 | credit_card | 3000 |
| 118 | 41 | 36 | bank_transfer | 900 |
| 119 | 51 | 46 | bank_transfer | 800 |
| 120 | 61 | 54 | credit_card | 1800 |
| 121 | 71 | 62 | gift_card | 1400 |
| 122 | 81 | 71 | credit_card | 500 |
| 123 | 91 | 79 | credit_card | 900 |
| 124 | 101 | 88 | credit_card | 2900 |
| 125 | 111 | 97 | bank_transfer | 1400 |

These 12 rows add up to **$180.00**, and 1852.00 − 180.00 = **1672.00**, which matches the settlement total exactly. Order 1 is the case finance flagged: payments 1 and 114 are both $10 on a credit card, so `orders.amount` = 20.00.

**Why no test failed:** `unique_stg_payments_payment_id` only checks the surrogate `id`, and the duplicates have new ids. Nothing checks the natural key, and nothing reconciles totals between models.

**The duplicates also corrupt other models:**
- `orders.amount`, the `*_amount` columns, and `fees` for those 12 orders. Fees are overstated because the duplicate charges get fee-rated too.
- `customers.customer_lifetime_value` (1852) and `completed_lifetime_value`.
- `region_revenue`, which sums to 1852.
- `daily_revenue` has a different problem: it holds a **mix** of states. It sums to 1686, not 1852. The older days were built before the duplicates arrived, so they still show correct values: Jan 496 vs 570 is −74 (orders 1, 10, 18, 26), Feb is −35, and Mar is −57. The duplicates only reached the rows rebuilt by the 3-day lookback window, which is April 7. That is the 1686 − 1672 = $14 duplicate on order 97. This also shows the incremental model silently disagrees with `orders`.

## 2. Fix

**Primary fix (data):** delete rows 114–125 from `seeds/raw_payments.csv`. Before deleting, confirm with finance or the processor that these are replayed records and not real second charges. The settlement total says they weren't settled. Then rebuild, with a full refresh so `daily_revenue` is consistent with `orders`:

```bash
dbt build --full-refresh
```

Expected result: `sum(orders.amount)` = 1672.00, and order 1 = 10.00.

**Optional defensive guard** in `models/staging/stg_payments.sql`, if the feed can replay batches:

```sql
renamed as (

    select
        id as payment_id,
        order_id,
        payment_method,

        -- `amount` is currently stored in cents, so we convert it to dollars
        amount / 100 as amount

    from source

    -- the feed can replay payments under new ids; keep the first occurrence
    qualify row_number() over (partition by order_id, payment_method, amount order by id) = 1

)
```

Only add this if it's true that a legitimate split payment can never repeat the same method and amount on one order. No such case exists in the current data. If you can't guarantee it, fix the source and rely on the tests below instead.

## 3. Tests that would have caught it

**a) Natural-key uniqueness on payments.** Add as `tests/assert_no_duplicate_payments.sql`:

```sql
-- the same charge should not appear twice under different payment ids
select
    order_id,
    payment_method,
    amount,
    count(*) as payments
from {{ ref('stg_payments') }}
group by order_id, payment_method, amount
having count(*) > 1
```

This test returns exactly the 12 pairs listed above. If `dbt_utils` is installed, you can use `dbt_utils.unique_combination_of_columns` on `stg_payments` for the same check.

**b) Cross-model reconciliation.** Add as `tests/assert_revenue_reconciles.sql`:

```sql
-- every revenue rollup should agree with orders
with totals as (
    select
        (select sum(amount) from {{ ref('orders') }}) as orders_total,
        (select sum(revenue) from {{ ref('daily_revenue') }}) as daily_total,
        (select sum(revenue) from {{ ref('region_revenue') }}) as region_total,
        (select sum(customer_lifetime_value) from {{ ref('customers') }}) as customers_total
)

select *
from totals
where abs(orders_total - daily_total) > 0.005
   or abs(orders_total - region_total) > 0.005
   or abs(orders_total - customers_total) > 0.005
```

On the current build, this test fails with 1852 vs 1686. It would also catch future drift in `daily_revenue` from late-arriving payments older than the 3-day window, which is a separate design weakness in that model.

The strongest check is to reconcile against the processor settlement itself. If you load settlement totals as a seed or source, add a test that compares them to `sum(orders.amount)` per day or per processor.
