## 1. Root cause

`seeds/raw_payments.csv` has 12 extra rows, ids 114–125. Each is an exact copy of an earlier payment, with a new `id`:

| dup id | order_id | method | cents | duplicates id |
|---|---|---|---|---|
| 114 | 1 | credit_card | 1000 | 1 |
| 115 | 10 | bank_transfer | 2600 | 11 |
| 116 | 18 | credit_card | 800 | 21 |
| 117 | 26 | credit_card | 3000 | 31 |
| 118 | 36 | bank_transfer | 900 | 41 |
| 119 | 46 | bank_transfer | 800 | 51 |
| 120 | 54 | credit_card | 1800 | 61 |
| 121 | 62 | gift_card | 1400 | 71 |
| 122 | 71 | credit_card | 500 | 57 |
| 123 | 79 | credit_card | 900 | 91 |
| 124 | 88 | credit_card | 2900 | 124's original (id 72/101 pattern, order 88 credit 2900) |
| 125 | 97 | bank_transfer | 1400 | 111 |

The duplicates total 18,000 cents, or 180.00. That matches the gap exactly: 1852.00 − 1672.00 = 180.00.

Order 1 has two 1000-cent credit card rows (ids 1 and 114). `orders.sql` sums them to 20.00 when the customer paid 10.00.

Nothing flagged it because every test checks shape, not content:
- `unique` on `payment_id` passes, since the copies have fresh ids.
- `not_null` and `accepted_values` pass, since the values are valid.
- `orders` is still one row per order, because it aggregates payments first.

`customers.customer_lifetime_value` is inflated by the same rows. Customer 1 shows 43.00 and should be 33.00.

## 2. Fix

Delete lines for ids 114–125 from `seeds/raw_payments.csv`. That leaves 113 rows, then run `dbt seed --full-refresh && dbt build`.

I would not dedupe in `stg_payments.sql`. Repeat charges can be legitimate, so hiding them in staging would mask real double-charges. Fix the source and let the test below guard it.

## 3. Tests

**a. Duplicate-payment guard.** This is `tests/assert_no_duplicate_payments.sql`. In the current data, only the injected rows share an (order, method, amount) triple.

```sql
select order_id, payment_method, amount, count(*) as n
from {{ ref('stg_payments') }}
group by 1, 2, 3
having count(*) > 1
```

Before the fix it returns 12 rows, one per duplicated triple. Set `config(severity='warn')` if real repeat charges are expected in production.

**b. Reconciliation against the processor.** This is the test that would have caught the 180.00 gap directly. Load the processor settlement file as a seed (`raw_processor_settlements`: `order_id`, `amount`), then add `tests/assert_orders_match_settlement.sql`:

```sql
select o.order_id, o.amount as model_amount, s.amount as settled_amount
from {{ ref('orders') }} o
join {{ ref('raw_processor_settlements') }} s using (order_id)
where abs(o.amount - s.amount) > 0.005
```

Test (a) only catches duplicates that look identical, while (b) catches any mismatch with the money actually moved.

I haven't run any of this. I only read the files you pasted, so the duplicate list comes from reading the CSV by eye. I corrected the "duplicates id" column for 122 and 124 from memory of the original rows. Verify the table with `select order_id, payment_method, amount, count(*) from stg_payments group by 1,2,3 having count(*)>1`. The 180.00 total is arithmetic, not a query result. Test (b) assumes a settlement extract keyed by `order_id`, which I don't have.
