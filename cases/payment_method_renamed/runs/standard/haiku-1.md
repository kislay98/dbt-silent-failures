claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 32 s | USD 0.006 | 2026-10-14 | project ad0b16b | prompt 57278f2875bf

## 1. Root cause

The bug is in the raw payments data, not in the SQL logic. The model passes it through without checking it.

- **`raw_payments` (loaded by the ingestion job)** has two method columns, `payment_method` and `method`. For some payments `payment_method` is NULL while `method` holds the value (for example, payment 86 and order 76, payment 65 and order 57, and payment 81 and order 71 in the `stg_payments` sample). The `method` column looks like the field the processor now sends. The ingestion job appears to have started writing to it and stopped populating `payment_method` for these rows.
- **`stg_payments.sql`** selects `payment_method` directly and does nothing with the NULLs.
- **`orders.sql`** sums `amount` for every payment, so `amount` and `total_amount` still include the NULL-method payments. The per-method `case` expressions match nothing for those rows, so every method column is 0. The fee `coalesce(fee_rate, 0)` also gives those payments a fee of 0.
- **The tests miss it.** `accepted_values` on `stg_payments.payment_method` ignores NULLs, so it passes. The `not_null` tests on `orders.*_amount` pass because the value is 0, not NULL. Nothing checks that the method columns add up to `amount`, and there is no `not_null` on `stg_payments.payment_method`.

Evidence for the scope:
- **January and February reconcile exactly.** January: 218 + 90 + 136 + 52 = 496. February: 295 + 17 + 74 + 29 = 415. Both equal `amount`, so no NULL-method payments exist there.
- **March has 0 for all four methods**, including coupon and gift card. That only happens if every March payment has no matching method, so this is not limited to card payments.
- **April has the same defect and finance has not reported it yet.** It shows 139.00 of amount with 0 for every method and 0.00 fees. Order 85 (April 9) has a NULL `payment_method` in `raw_payments`.
- **Fees are also wrong for March and April** (0.00), because the fee is also keyed on the method.

I can't see the full March payment rows from here, so I haven't confirmed that the 334.00 processor card volume equals the NULL-method card payments. The pattern fits, and that is the check to run first:

```sql
select date_trunc('month', o.order_date) as month,
       count(*) as null_method_payments,
       sum(p.amount) as null_method_amount,
       count(*) filter (where p.method = 'credit_card') as card_rows
from raw_payments p
join raw_orders o on o.id = p.order_id
where p.payment_method is null
group by 1 order by 1;
```

It should show nothing before March, and March and April should account for the 622 and 139 gaps. Also check that `payment_method = method` wherever both are non-NULL. If it never differs, `method` is a safe fallback.

Unaffected: `daily_revenue` (no method logic), `region_revenue`, and `customers`.

## 2. Fix

**Upstream first.** Have the ingestion owner confirm which column is canonical and fix the loader so `payment_method` is populated. Then backfill the raw table:

```sql
update raw_payments
set payment_method = method
where payment_method is null
  and method is not null;
```

**Model guard.** In `models/staging/stg_payments.sql`, use the fallback so the model is correct even if the loader regresses:

```sql
select
    id as payment_id,
    order_id,
    coalesce(payment_method, method) as payment_method,
    amount / 100 as amount
from source
```

Then rebuild everything downstream of `stg_payments`, including `orders` and `customers`:

```
dbt build --select stg_payments+
```

`daily_revenue` is an incremental model, but it doesn't depend on the method, so its numbers should not change.

## 3. Test that would have caught it

Add two tests.

**a. Not-null on the staging column**, in `models/staging/schema.yml`:

```yaml
  - name: stg_payments
    columns:
      - name: payment_method
        tests:
          - not_null
          - accepted_values:
              arguments:
                values: ['credit_card', 'coupon', 'bank_transfer', 'gift_card']
```

This fails on the current data, because `accepted_values` skips NULLs and `not_null` does not.

**b. A reconciliation singular test**, in `tests/assert_payment_methods_sum_to_amount.sql`. This catches any payment whose method is missing from the breakdown, including new or misspelled methods:

```sql
select
    order_id,
    amount,
    credit_card_amount + coupon_amount + bank_transfer_amount + gift_card_amount as methods_total
from {{ ref('orders') }}
where abs(amount - (credit_card_amount + coupon_amount + bank_transfer_amount + gift_card_amount)) > 0.005
```

The current data would have failed this test for every March and April order, so the nightly build would have stopped before finance saw the report.
