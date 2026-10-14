claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 36 s | USD 0.007 | 2026-10-14 | project 7d5b525+dirty

## 1. Root cause

`jaffle_shop/seeds/raw_payments.csv`, rows with `id` 114–125, are duplicates of existing payments. Each has the same `order_id`, `payment_method`, and `amount` as an earlier row, but a new `id`. They're appended as a block after id 113, which looks like a loader re-run.

| dup id | order_id | method | amount (cents) | original id |
|---|---|---|---|---|
| 114 | 1 | credit_card | 1000 | 1 |
| 115 | 10 | bank_transfer | 2600 | 11 |
| 116 | 18 | credit_card | 800 | 21 |
| 117 | 26 | credit_card | 3000 | 31 |
| 118 | 36 | bank_transfer | 900 | 41 |
| 119 | 46 | bank_transfer | 800 | 51 |
| 120 | 54 | credit_card | 1800 | 61 |
| 121 | 62 | gift_card | 1400 | 71 |
| 122 | 71 | credit_card | 500 | 81 |
| 123 | 79 | credit_card | 900 | 91 |
| 124 | 88 | credit_card | 2900 | 101 |
| 125 | 97 | bank_transfer | 1400 | 111 |

The 12 rows total 18000 cents, or 180.00. That is exactly the gap: 1852.00 − 180.00 = 1672.00.

The chain of failure:
- `stg_payments` converts cents to dollars and keeps all 125 rows.
- `orders.sql` sums every payment per order with no dedupe, so orders 1, 10, 18, 26, 36, 46, 54, 62, 71, 79, 88, and 97 are overstated. Order 1 shows 20.00 instead of 10.00.
- `customers`, `daily_revenue`, and `region_revenue` all read from those inflated figures. For example, customer 7 shows `customer_lifetime_value` 52.0, but its only order (10) should total 26.0. Customer 1 shows 43.0, which should be 33.0.

Why the tests pass: `payment_id` is unique because the duplicates have new ids. `order_id` is not unique in payments by design, since split payments are legitimate. Nothing checks `(order_id, payment_method, amount)`, and nothing reconciles the output to the processor settlement total.

I checked this by hand against the seed data. I did not run `dbt build`, so the arithmetic is the verification.

## 2. Fix

**Data fix (primary).** Before deleting anything, confirm with the payment processor that these 12 charges are not real. Finance's 10.00 charge for order 1 already points that way. Then remove rows 114–125 from `raw_payments.csv`.

After that, run:

```
dbt build --full-refresh -s daily_revenue
```

Without `--full-refresh`, the incremental `daily_revenue` only rebuilds the last 3 days. Older dates would keep the inflated revenue.

**Optional code guard.** If the feed can re-send payments again, dedupe in `stg_payments.sql` after you have confirmed the rule. Keep the earliest row for each business key:

```sql
renamed as (

    select
        id as payment_id,
        order_id,
        payment_method,
        amount / 100 as amount

    from source

),

deduped as (

    select *
    from renamed
    qualify row_number() over (
        partition by order_id, payment_method, amount
        order by payment_id
    ) = 1

)

select * from deduped
```

Only add this if the processor confirms that same-order, same-method, same-amount payments are never legitimate. I checked the current seed, and the only such groups are the 12 duplicates above.

Expected results after the fix: `sum(orders.amount)` = 1672.00, order 1 = 10.00, customer 7 `customer_lifetime_value` = 26.0, customer 1 = 33.0, customer 64 = 30.0.

## 3. Test that would have caught this

Add a singular test at `jaffle_shop/tests/assert_no_duplicate_payments.sql`. It needs no extra packages and fails on the current data, listing the 12 duplicate groups:

```sql
select
    order_id,
    payment_method,
    amount,
    count(*) as n_rows
from {{ ref('stg_payments') }}
group by 1, 2, 3
having count(*) > 1
```

Stronger control: seed the processor's settlement total (per order or per day) from its export and add a singular test that compares it with `sum(amount)` from `stg_payments`. That catches duplicates, dropped rows, and unit errors, and it is the check finance actually performs.

## Other things I noticed

- `customers` in `schema.yml` documents a `total_order_amount` column that the model does not produce. Remove it or add it to the model.
- `region_revenue` joins payments to orders to regions, so it inherits the duplicate payments. Its `revenue` will also change after the fix.

I can apply the seed deletion, the singular test, and the schema cleanup now if you want. I'd hold off on the `qualify` dedupe until the processor confirms the rule.
