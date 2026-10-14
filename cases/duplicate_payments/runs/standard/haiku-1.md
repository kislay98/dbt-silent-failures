claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 47 s | USD 0.010 | 2026-10-14 | project ad0b16b | prompt b041d9ee24db

## 1. Root cause

**The duplicate payments come from `jaffle_shop/seeds/raw_payments.csv`, rows with `id` 114–125 (file lines 115–126).** Each one repeats an earlier payment with the same `order_id`, `payment_method`, and `amount`, but has a new `id`. They look like processor re-deliveries appended to the feed:

| Duplicate id | Copies id | order_id | payment_method | amount (AUD) |
|---|---|---|---|---|
| 114 | 1 | 1 | credit_card | 10.00 |
| 115 | 11 | 10 | bank_transfer | 26.00 |
| 116 | 21 | 18 | credit_card | 8.00 |
| 117 | 31 | 26 | credit_card | 30.00 |
| 118 | 41 | 36 | bank_transfer | 9.00 |
| 119 | 51 | 46 | bank_transfer | 8.00 |
| 120 | 61 | 54 | credit_card | 18.00 |
| 121 | 71 | 62 | gift_card | 14.00 |
| 122 | 81 | 71 | credit_card | 5.00 |
| 123 | 91 | 79 | credit_card | 9.00 |
| 124 | 101 | 88 | credit_card | 29.00 |
| 125 | 111 | 97 | bank_transfer | 14.00 |

The duplicates total 180.00. That accounts for the whole gap: 1852.00 − 180.00 = **1672.00**, the finance figure.

Order 1 is the example from the report. It has two credit_card payments of 10.00 each (ids 1 and 114), so `orders.amount` is 20.00, but the customer was charged once, for 10.00.

The error flows through `stg_payments` into `orders.amount` (a per-order sum), then into `customers`, `region_revenue`, and `daily_revenue`. `customers.completed_lifetime_value` is also inflated, and so are `orders.fees`.

**Why every test passed:**
- `stg_payments.payment_id` is unique, because each duplicate has its own id. The uniqueness test checks the wrong key.
- Nothing tests `orders.amount` against any independent number. `orders.amount` is derived from `stg_payments`, so it agrees with its own input by construction.

**A second, separate problem in `daily_revenue`:** its `revenue` totals 1686.00, not 1852.00. The gap is 166.00, spread over Jan–Mar. The incremental filter only rebuilds the last three days, so older rows are probably stale from an earlier build. I can't confirm this from the log alone. A `--full-refresh` will show whether the gap closes.

## 2. Fix

**Data fix:** delete rows `id` 114–125 from `jaffle_shop/seeds/raw_payments.csv`. Then rebuild:

```sh
dbt seed
dbt build --full-refresh -s daily_revenue
dbt build
```

Expected after the rebuild:
- `sum(orders.amount)` = 1672.00
- order 1 `amount` = 10.00
- `sum(region_revenue.revenue)` = 1672.00
- `sum(daily_revenue.revenue)` = 1672.00

I'd remove the rows rather than dedupe silently in staging. A dedupe on `(order_id, payment_method, amount)` would also drop legitimate repeat payments, and it would hide the problem. If the seed stands in for a real processor feed, the proper fix is upstream: dedupe on the processor's transaction reference, which these rows don't carry.

## 3. Tests that would have caught it

**a) A natural-key uniqueness test on `stg_payments`** (in `jaffle_shop/models/staging/schema.yml`). This needs the `dbt_utils` package. On the current data it fails on the 12 rows above, and because it's a model-level test, `dbt build` stops before the downstream models.

```yaml
  - name: stg_payments
    columns:
      - name: payment_id
        tests:
          - unique
          - not_null
      ...
    tests:
      - dbt_utils.unique_combination_of_columns:
          arguments:
            combination_of_columns: ['order_id', 'payment_method', 'amount']
```

**b) A reconciliation test against an independent control total.** This is the test that would have matched finance's 1672.00. It needs a seed or source with the processor's settlement total, which I'll call `raw_processor_settlements` here. Add it to `tests/`:

```sql
-- tests/assert_orders_match_settlement_total.sql
with modelled as (
    select sum(amount) as total from {{ ref('orders') }}
),

settled as (
    select sum(settled_amount) as total from {{ ref('raw_processor_settlements') }}
)

select modelled.total as modelled_total, settled.total as settled_total
from modelled
cross join settled
where abs(modelled.total - settled.total) > 0.005
```

Test (a) catches this class of bug at the source. Test (b) catches any duplicated input, because nothing internal can detect it. A reconciliation test against `orders` alone would have passed here. The same check, `sum(daily_revenue.revenue)` against `sum(orders.amount)`, would have caught the stale-history problem.

**Other things I noticed in `customers` docs:** `schema.yml` documents `total_order_amount`, which doesn't exist (the column is `customer_lifetime_value`), and `customer_lifetime_value` itself is undocumented.

I haven't edited any files. Do you want me to apply the seed fix and add the tests? For the settlement test I'd need the processor's per-day or total figure from finance.
