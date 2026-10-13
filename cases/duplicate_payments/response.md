## 1. Root cause

`seeds/raw_payments.csv` has 12 duplicated payments appended at the end (ids 114-125). Each one repeats an earlier payment for the same order, method and amount under a new `id`.

| dup id | order_id | method | cents | original id |
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

These add up to 18,000 cents, or $180.00, and 1852.00 − 180.00 = 1672.00, which is exactly the settlement total. Order 1 shows 20.00 instead of 10.00 because of id 114. Order 10 is also doubled (52 instead of 26), and customer 1's lifetime value is 43 instead of 33.

Every test passes because the only uniqueness check on payments is on `payment_id`, and the duplicates have fresh ids. `orders.sql` and `customers.sql` just `sum(amount)`, so the duplicates flow straight through. Nothing in the project compares against an external total.

## 2. Fix

Delete lines for ids 114-125 from `seeds/raw_payments.csv` (the file should end at id 113), then run `dbt build`. Afterwards `sum(amount)` should come out at 1672.00.

I did not add a dedupe in `stg_payments`. Orders can legitimately have several payments by the same method (e.g. order 18 has 500 and 800 on credit card), so a blanket dedupe could hide real payments. I'm inferring that these rows are duplicates from the exact reconciliation to $180, so it's worth confirming with whoever loaded the file or against the processor's records.

## 3. Tests that would have caught it

Add a uniqueness check on the natural key to `models/staging/schema.yml`. No legitimate payment in the current data shares order, method and amount, so it passes on clean data and fails on all 12 duplicates. It needs `dbt_utils`.

```yaml
  - name: stg_payments
    tests:
      - dbt_utils.unique_combination_of_columns:
          arguments:
            combination_of_columns: [order_id, payment_method, amount]
```

Also add a reconciliation test against the processor's settlement figure. This one would have flagged the problem directly. It assumes a `processor_settlements` seed or source with `order_id` and `settled_amount`, which doesn't exist in this project yet:

```sql
-- tests/assert_orders_amount_matches_processor_settlement.sql
select o.order_id, o.amount, s.settled_amount
from {{ ref('orders') }} o
join {{ ref('processor_settlements') }} s using (order_id)
where abs(o.amount - s.settled_amount) > 0.005
```

I haven't edited any files or run `dbt build`. The numbers above come from reading the CSV and the output you pasted.
