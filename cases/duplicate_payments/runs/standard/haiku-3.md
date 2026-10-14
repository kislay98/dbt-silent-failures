claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 59 s | USD 0.011 | 2026-10-14 | project ad0b16b | prompt b041d9ee24db

## 1. Root cause

**The duplicate payments are in `jaffle_shop/seeds/raw_payments.csv`, rows id 114 through 125.** Each of those 12 rows repeats an earlier row exactly, with the same `order_id`, `payment_method`, and `amount`, but a new `id`:

| Duplicate id | Copy of id | order_id | payment_method | amount (cents) |
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

The duplicates total 18,000 cents, or **180.00**. That is exactly the gap: 1852.00 − 1672.00 = 180.00. The original 113 payments sum to 1672.00, which matches the processor settlement.

Order 1 shows the problem directly. Payments id 1 and id 114 are both `credit_card` 1000, so the order totals 20.00, but the customer was charged 10.00.

**How it propagates:**
- `stg_payments` passes the rows through with `amount / 100`, so the duplicates survive.
- `orders.amount` is `sum(payments.amount)`, so it is inflated for orders 1, 10, 18, 26, 36, 46, 54, 62, 71, 79, 88, and 97.
- `orders.fees` is inflated too, because it is computed from the same payment rows.
- `customers.customer_lifetime_value` and `completed_lifetime_value` are summed from payments, so they are inflated.
- `region_revenue.revenue` sums `payments.amount` directly, so it is inflated. Its total is 1852.
- `daily_revenue` is also affected, as described next.

**Why every test passed:** `payment_id` is unique because the duplicate rows have new ids. Nothing checks for repeated payments per order, and nothing reconciles `orders.amount` to an independent source like the processor. The only tests on `amount` are `not_null`.

**A second, separate problem in `daily_revenue.sql`:** the output does not reconcile to `orders` even before the duplicates are considered.
- Monthly `daily_revenue` totals are 496 (Jan), 415 (Feb), 622 (Mar), and 153 (Apr). They sum to 1686, not 1852.
- April matches `orders` exactly, because the duplicate for order 97 falls in the rebuilt window.
- Jan through Mar do not match. Those dates are older than the 3-day window the model rebuilds on each run, so they hold stale values from an earlier run.

The 3-day window cannot see corrections to old orders, and these duplicates are on orders from January through April.

I answered from the files and output you pasted. I did not run any queries or commands, so the stale-history explanation is inferred from the numbers and should be confirmed.

## 2. Fix

**Data change:** remove rows id 114–125 from `seeds/raw_payments.csv`. Before deleting, confirm with the processor that these are re-sent copies and not real second charges. The 10.00 charged on order 1 is consistent with duplicates.

After the change, the expected results are:
- `sum(orders.amount)` = 1672.00
- Monthly `orders.amount`: Jan 526, Feb 403, Mar 604, Apr 139
- Order 1 `amount` = 10.00

**Rebuild `daily_revenue` with a full refresh.** Otherwise the Jan–Mar rows stay stale:

```
dbt seed
dbt build --full-refresh
```

**Longer term:** the 3-day incremental window will keep missing late corrections to old orders. Either materialize `daily_revenue` as a table, or schedule a periodic `--full-refresh`.

I would not dedupe in `stg_payments` on `(order_id, payment_method, amount)`. That would silently drop a real second payment of the same amount, such as two 10.00 card payments on one order. The duplicates should be fixed at the feed and caught by a test.

## 3. Test that would have caught it

Add a generic test that fails on repeated payments for the same order, method, and amount. It goes in `macros/` (any generic test works there), and it is wired into `models/staging/schema.yml`:

```sql
{% test no_duplicate_payments(model, columns) %}

select {{ columns | join(', ') }}, count(*) as n
from {{ model }}
group by {{ columns | join(', ') }}
having count(*) > 1

{% endtest %}
```

```yaml
  - name: stg_payments
    tests:
      - no_duplicate_payments:
          arguments:
            columns: ['order_id', 'payment_method', 'amount']
    columns:
      - name: payment_id
        ...
```

On the current data this returns 12 groups and fails the build.

The stronger control is a reconciliation test, which is the check finance is doing by hand. It needs a processor settlement seed, which the project does not have yet. It would compare `sum(orders.amount)` per order against the settlement feed and fail on any mismatch. It would also catch the stale `daily_revenue` rows, since they would no longer agree with `orders`.

Would you like me to apply the seed edit, add the generic test, and add a `daily_revenue` reconciliation test?
