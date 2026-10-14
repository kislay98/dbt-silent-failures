claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 18 s | USD 0.061 | 2026-10-14 | project f769fdf+wk6ext | prompt b430ab6cd05c

## 1. Root cause

`raw_payment_methods` (the seed or ingested table) has more than one row for `bank_transfer`. `models/orders.sql` joins `stg_payments` to it with `left join payment_methods on payments.payment_method = payment_methods.payment_method`, so every bank transfer payment is duplicated. I can't see the seed data, but the numbers fit a second `bank_transfer` row, probably under a different `processor`.

- Only `bank_transfer` is affected, because only that method has a duplicate row. Credit card, coupon and gift card match the statements.
- `stg_payments` is correct: bank transfer totals 411.00 there. `orders` shows 822.00, exactly double.
- Order 5 has one payment of 17.00, but `orders` shows 34.00. Order 25 has 20 + 22 + 16 = 58, but `orders` shows 40 + 22 + 16 = 78.
- The total overstatement is 2083 - 1672 = 411.00, which is exactly the bank transfer total.
- `region_revenue` and `customers` read `stg_payments` directly and never touch `raw_payment_methods`, so they still total 1672.00.
- `fees` is also wrong for bank transfers, because the fan-out multiplies them as well. If the two rows have different `fee_rate` values, it also depends on which one is correct.
- The tests all pass because nothing checks the grain. The seed's schema only has `not_null` on `payment_method` and `fee_rate`, with no `unique` on `payment_method`. `orders.order_id` stays unique because the `group by` collapses the fan-out, so that test can't see it either.

## 2. Fix

First, look at the duplicate:

```sql
select * from raw_payment_methods
where payment_method in (
    select payment_method from raw_payment_methods
    group by 1 having count(*) > 1
);
```

1. In the ingestion source, remove the stale or incorrect `bank_transfer` row so there is one row per `payment_method`. The table has no effective-date column, so someone who knows which processor and fee rate is current has to pick the row. I can't tell which from what you've shown.
2. Rebuild everything, including `daily_revenue`. It only rebuilds the last three days, so the older days keep the doubled revenue:
   ```
   dbt build --full-refresh
   ```
3. Optionally, make `orders.sql` immune to this. Join on a pre-aggregated rate, or compute fees at payment level before aggregating. I wouldn't hide a bad reference table, though. A failing test is better than silently picking a row.

After the rebuild, `sum(orders.amount)` should be 1672.00 and bank transfer should be 411.00. The fees should be rechecked against the processor's fee statement.

## 3. Tests that would have caught it

Add to `models/schema.yml`:

```yaml
  - name: raw_payment_methods
    columns:
      - name: payment_method
        tests:
          - not_null
          - unique
```

Add a reconciliation test for the fan-out itself, in `tests/assert_orders_amount_matches_payments.sql`:

```sql
select
    o.total as orders_total,
    p.total as payments_total
from (select sum(amount) as total from {{ ref('orders') }}) o
cross join (select sum(amount) as total from {{ ref('stg_payments') }}) p
where abs(o.total - p.total) > 0.005
```

The first test catches the duplicate at its source. The second catches any future join fan-out in `orders`, and it would have failed on this data with 2083.00 against 1672.00.
