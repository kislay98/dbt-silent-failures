claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 18 s | USD 0.059 | 2026-10-14 | project f769fdf+wk6ext | prompt b430ab6cd05c

## 1. Root cause

**`seeds/raw_payment_methods.csv` has more than one row for `bank_transfer`** (my inference, since I can't see the seed data). `models/orders.sql` then fans out on it.

The evidence:
- `stg_payments` totals exactly 1672.00, and `bank_transfer` there is 411.00. So staging is right.
- `orders.amount` is 2083.00, which is 1672 + 411. The excess is exactly one extra copy of the bank transfer money.
- Order 5 has a single 17.00 bank transfer payment but shows 34.00. Order 25 has 20 + 22 + 16 = 58 but shows 78, which is 58 + 20. Only bank transfer payments are doubled.
- `region_revenue` and `customers` read `stg_payments` directly and never join the seed, so they still total 1672.00.

In `orders.sql`, the `order_payments` CTE does `payments left join payment_methods on payment_method`. If the seed has two `bank_transfer` rows (for example one per processor), every bank transfer payment is duplicated before the `sum(...)`. The `fees` column is also wrong for bank transfers, because `amount * fee_rate` is summed over both rates.

No test caught it because the seed tests only check `not_null`. Nothing tests uniqueness of the join key or reconciles `orders` to `stg_payments`. The `left join` hides the problem silently.

`daily_revenue` is affected too. It is incremental and only rebuilds the last 3 days, so older dates keep the inflated revenue until a full refresh.

## 2. Fix

1. Open `seeds/raw_payment_methods.csv` and look at the duplicate `bank_transfer` rows. `raw_payments` has no processor column, so the model can't tell which row applies to which payment. The data owner has to say which row is current, and you keep only that one. The seed has no effective-date column, so I can't pick it for you.
2. Reload and rebuild, with a full refresh so `daily_revenue` is rebuilt for all dates:
   ```
   dbt seed --select raw_payment_methods
   dbt build --full-refresh
   ```
3. Check that `sum(orders.amount)` is 1672.00 and the `bank_transfer` column is 411.00. Fees will also change, so have finance confirm them.

If you want `orders.sql` to be robust regardless of the seed, compute fees in a separate step. That way a bad rate table can't change the amounts.

## 3. Tests

Add a uniqueness test on the seed key in `models/schema.yml`:

```yaml
  - name: raw_payment_methods
    columns:
      - name: payment_method
        tests:
          - not_null
          - unique
```

Add a reconciliation test that catches fan-out in any form, as `tests/assert_orders_amount_matches_payments.sql`:

```sql
select
    o.total as orders_total,
    p.total as payments_total
from (select sum(amount) as total from {{ ref('orders') }}) o
cross join (select sum(amount) as total from {{ ref('stg_payments') }}) p
where abs(o.total - p.total) > 0.005
```

A per-method version, comparing `bank_transfer_amount` and the others against `stg_payments`, would show which column is off. Either test would have failed on the first build with this seed.
