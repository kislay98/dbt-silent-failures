claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 25 s | USD 0.096 | 2026-10-14 | project 7d5b525+dirty

## 1. Root cause

`models/staging/stg_payments.sql` divides every payment by 100 (`amount / 100 as amount`), on the assumption that the whole feed is in cents. That holds for every payment method except `gift_card`. In `seeds/raw_payments.csv`, the 12 gift card rows are already in whole dollars:

| payment ids | order ids | raw amounts |
|---|---|---|
| 9, 22, 26, 48, 49, 67, 68, 71, 92, 99, 107, 110 | 9, 19, 23, 43, 44, 58, 59, 62, 80, 87, 93, 96 | 23, 6, 23, 18, 11, 6, 28, 14, 3, 30, 26, 17 |

Those rows get divided by 100 as well, so 205 becomes 2.05. For example, payment 9 (order 9) is raw `23`, which the model turns into 0.23.

The numbers reconcile exactly:
- **Gift cards:** 205.00 on the provider statement becomes 2.05 in `orders.gift_card_amount`.
- **Total:** 871 + 411 + 185 + 205 = 1672.00 at the processor, against 1469.05 in `sum(orders.amount)`. The 202.95 gap is 205.00 − 2.05.

Every test passes because none of them check magnitudes. `not_null` and `accepted_values` are satisfied by 0.23 as readily as by 23.00. The error also flows into `customers` (lifetime values), `daily_revenue` and `region_revenue`.

## 2. Fix

Fix the unit conversion in `stg_payments.sql`:

```sql
        -- `amount` is stored in cents, except gift_card, which arrives in whole dollars
        case
            when payment_method = 'gift_card' then amount
            else amount / 100
        end as amount
```

If you control the feed, correcting gift card amounts at the source (multiplying those 12 seed rows by 100) is cleaner. The staging change is safer if you can't edit it.

Then rebuild with a full refresh, because `daily_revenue` is incremental and only reprocesses the last 3 days. Without the refresh, the older days keep the wrong revenue:

```
dbt build --full-refresh
```

After the rebuild, `sum(orders.amount)` should be 1672.00 and `sum(gift_card_amount)` should be 205.00. Order 9 should show 23.00.

## 3. Tests that would have caught it

**A unit test** that pins the intended unit conversion per payment method, in `models/staging/schema.yml` (dbt 1.8+):

```yaml
unit_tests:
  - name: stg_payments_amount_units
    model: stg_payments
    given:
      - input: ref('raw_payments')
        rows:
          - {id: 1, order_id: 1, payment_method: credit_card, amount: 1000}
          - {id: 2, order_id: 2, payment_method: gift_card, amount: 23}
    expect:
      rows:
        - {payment_id: 1, order_id: 1, payment_method: credit_card, amount: 10}
        - {payment_id: 2, order_id: 2, payment_method: gift_card, amount: 23}
```

**A reconciliation test** against the external statements, which is what finance actually compared. Add a seed `seeds/control_payment_totals.csv` with `payment_method,expected_total` (bank_transfer 411, coupon 185, credit_card 871, gift_card 205), plus `tests/assert_payment_totals_match_statements.sql`:

```sql
select
    c.payment_method,
    c.expected_total,
    sum(p.amount) as actual_total
from {{ ref('control_payment_totals') }} c
left join {{ ref('stg_payments') }} p using (payment_method)
group by 1, 2
having abs(coalesce(sum(p.amount), 0) - c.expected_total) > 0.005
```

The unit test catches regressions in the conversion logic. The reconciliation test catches any future unit mismatch in the data itself, including one the unit test doesn't cover. In production, load the control totals from the processor and provider statements instead of hardcoding them.

I haven't run any of this, since the project files weren't in my working directory. The diagnosis comes from the files and output you pasted.
