claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 39 s | USD 0.090 | 2026-10-14 | project f769fdf+wk6ext | prompt 86aed168df9e

## 1. Root cause

There are two independent defects. Their errors partly cancel in the total (+853 and -65), so the totals look only moderately wrong.

**a) Fan-out in `models/orders.sql`, which inflates credit card by +853.00**

`order_payments` does `left join payment_methods on payments.payment_method = payment_methods.payment_method`, assuming `raw_payment_methods` has one row per method. The evidence says credit_card has two rows there, though I can't see the table contents:

- `stg_payments` credit_card sums to 853.00 and `orders.credit_card_amount` sums to 1706.00, exactly 2x.
- Order 2 has one 20.00 payment and shows 40.00. Order 25 has a 22.00 card payment and shows 44.00.
- Bank transfer (386.00), coupon (169.00) and gift card (199.00) match staging exactly, so only credit_card is duplicated.
- `fees` on order 2 is 1.16 (40 x 0.029), so both duplicate rows carry the same rate.
- `region_revenue` and `customers` read `stg_payments` directly, so they show the true 1607.00 (853 + 386 + 169 + 199).

The seed has no uniqueness test on `payment_method`. Also, the seed tests in `schema.yml` never ran: 32 data tests were found but only 28 ran. The raw tables are loaded by ingestion, not built by dbt, so the `seeds:` tests were skipped.

**b) NULL amounts in `raw_payments`, which understate every method by 65.00 in total**

Eight rows have `amount` NULL: 3 credit_card, 3 bank_transfer, 1 coupon and 1 gift_card. They are the `NULL` rows in the staging sample (payments 15, 21, 30, 62, 67). `sum()` silently skips them, and the models never complain. Finance's gaps are:

| Method | Missing |
|---|---|
| credit_card | 18.00 (871 - 853) |
| bank_transfer | 25.00 |
| coupon | 16.00 |
| gift_card | 6.00 |
| **Total** | **65.00** (1672 - 1607) |

Order 13 was settled for 19.00. Staging has 5.00 of card plus a NULL bank transfer (about 14.00 missing). The model shows 10.00 because the 5.00 card payment is doubled.

The model-level `not_null` tests on `orders` can't catch this. Each `*_amount` is `sum(case ... else 0 end)`, which is never NULL, and `stg_payments.amount` has no test.

Check: 2460 = 1607 + 853, and 1672 = 1607 + 65.

## 2. Fix

**Duplicate rows (a).** Confirm first:
```sql
select payment_method, processor, fee_rate from raw_payment_methods
where payment_method in (select payment_method from raw_payment_methods group by 1 having count(*) > 1);
```
The seed has no effective-date column, so SQL can't pick the right row. The owner of the processor feed needs to remove the stale credit_card row. For a guard in the model, pre-aggregate the dimension to one row per method. This is only safe if the duplicate rows share a `fee_rate`, as the fees suggest:
```sql
payment_methods as (
    select payment_method, max(fee_rate) as fee_rate
    from {{ ref('raw_payment_methods') }}
    group by payment_method
),
```
After that, `orders.credit_card_amount` returns to 853.00 and the total to 1607.00.

**NULL amounts (b).** This is a data problem and not fixable in dbt. Backfill the 8 amounts from the processor statement, or have the ingestion job re-load them. Don't `coalesce(amount, 0)`, since that hides the gap. Once backfilled, the total should reach the 1672.00 settlement.

**Incremental model.** Run `dbt run --full-refresh -s daily_revenue` afterwards. It only rebuilds the last 3 days, so older dates keep the doubled revenue.

## 3. Tests

```yaml
# models/schema.yml (replaces the current raw_payment_methods block)
  - name: raw_payment_methods
    columns:
      - name: payment_method
        tests: [not_null, unique]

# models/staging/schema.yml, under stg_payments
      - name: amount
        tests: [not_null]
```
The `unique` test fails on the duplicate credit_card row, and `not_null` fails on the 8 missing amounts. For the seed tests to actually run, the raw tables need to be declared as `sources` with their own tests, since `dbt build` isn't running them as seeds.

A reconciliation test catches both defects, even if neither column test existed:

```sql
-- tests/assert_orders_reconcile_to_payments.sql
with o as (select order_id, amount from {{ ref('orders') }}),
p as (select order_id, sum(amount) as amount from {{ ref('stg_payments') }} group by 1)
select coalesce(o.order_id, p.order_id) as order_id, o.amount as order_amount, p.amount as payment_amount
from o full join p on o.order_id = p.order_id
where abs(coalesce(o.amount, 0) - coalesce(p.amount, 0)) > 0.005
```

Run on the current data, this returns orders 2, 13, 25 and every other credit-card order. Its coverage of the NULL-amount bug is limited, because both sides skip NULLs. The `not_null` on `stg_payments.amount` is what catches that one. For the processor tie-out, compare `sum(orders.amount)` per method against the processor statement.
