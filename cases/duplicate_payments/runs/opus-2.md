claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 31 s | USD 0.185 | 2026-10-14 | project f769fdf | prompt d246cb0f008e

# Root cause: 12 replayed payment rows in `raw_payments.csv`

## 1. Root cause

`jaffle_shop/seeds/raw_payments.csv` has 125 rows, but rows **id 114–125** are copies of earlier payments. Each copy has a new `id` but the same `order_id`, `payment_method` and `amount` as the original. The copied ids follow a clean pattern: every 10th payment, starting at 1. That looks like a re-ingested or replayed batch, not real charges.

| dup id | original id | order_id | method | cents |
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

The copies add 18,000 cents, which is **180.00**. That is exactly the gap: 1852.00 − 180.00 = 1672.00, the settlement total.

They flow through like this:

- `stg_payments` passes them through.
- `orders.sql` does `sum(amount) ... group by order_id`, so each affected order is doubled. Order 1 becomes 10 + 10 = 20.00, and order 10 becomes 26 + 26 = 52.00, as in the output sample.
- `customers` (customer 1 = 43.00, customer 7 = 52.00) and `region_revenue` inherit the inflated amounts.

All tests pass because none of them can see this:

- `unique_stg_payments_payment_id` passes because each copy has a fresh `id`.
- The rest are `not_null` and `accepted_values` checks, which these rows satisfy.

## 2. Fix

**Preferred: fix the data.** Delete rows 114–125 from `seeds/raw_payments.csv`, then run:

```
dbt build --full-refresh
```

`--full-refresh` matters because `daily_revenue` is incremental and only rebuilds the last 3 days. Without it, the inflated history for Jan–Apr stays in place. Also find out upstream why every 10th payment was replayed, or it will come back.

**Fallback, if you can't control the raw feed:** dedupe in `models/staging/stg_payments.sql`:

```sql
renamed as (

    select
        id as payment_id,
        order_id,
        payment_method,

        -- `amount` is currently stored in cents, so we convert it to dollars
        amount / 100 as amount

    from source

    -- the payments feed has replayed rows under new ids; keep the first copy
    qualify row_number() over (
        partition by order_id, payment_method, amount
        order by id
    ) = 1

)
```

The risk with this: a customer who really makes two identical split payments on one order would lose one of them. No such case exists in today's clean data (order 18's split is 500 + 800, and order 79's is 1800 + 900). Still, fixing the data at the source is safer.

## 3. A test that would have caught it

Add a singular test, `tests/assert_no_duplicate_payments.sql`:

```sql
-- A payment should not appear twice under different ids
select
    order_id,
    payment_method,
    amount,
    count(*) as n_payments
from {{ ref('stg_payments') }}
group by 1, 2, 3
having count(*) > 1
```

On the current data this returns 12 rows and fails the build. After the fix it returns none.

If you use `dbt_utils`, the same check as a column-combination test on `stg_payments` is:

```yaml
    data_tests:
      - dbt_utils.unique_combination_of_columns:
          arguments:
            combination_of_columns: [order_id, payment_method, amount]
```

Because a real identical split payment would also trip this test, consider `config: {severity: warn}`. For a stronger guarantee, add a reconciliation test that compares `sum(orders.amount)` with the processor's settlement total, if you can load that as a source or seed. That is the check finance did by hand.

**Minor, unrelated:** `models/schema.yml` documents a `customers.total_order_amount` column that doesn't exist; the model outputs `customer_lifetime_value`.

I worked this out by hand from the files you pasted; I haven't run dbt. The 180.00 total matches the reported gap exactly.
