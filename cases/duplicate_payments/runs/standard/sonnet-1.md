claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 27 s | USD 0.124 | 2026-10-14 | project ad0b16b | prompt b041d9ee24db

## 1. Root cause

`seeds/raw_payments.csv` contains 12 duplicated payment rows, ids 114-125. Each one repeats an earlier row with the same `order_id`, `payment_method` and `amount` but a new `id`:

| dup id | order | method | cents | original id |
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

- **Size of the gap:** they total 18,000 cents, which is $180.00, exactly the 1852.00 − 1672.00 difference.
- **Order 1:** it has payments 1 and 114 at $10 each, so `orders.amount` is 20.00 against the 10.00 charged.
- **Why the build is green:** `stg_payments.payment_id` is unique because the duplicates have fresh ids. No test looks at the business key.
- **Downstream impact:** the extra $180 flows into `orders.amount`, the per-method columns, `orders.fees` (processor fees are overstated), `customers.customer_lifetime_value` and `region_revenue.revenue`.

**Second symptom:** `daily_revenue` is wrong in the other direction. It shows Jan 496 against 570 in `orders`, Feb 415 against 450, and Mar 622 against 679. The gaps (74, 35, 57) are exactly the duplicates falling in each month. It was built before the duplicates landed, and the incremental filter only rebuilds the last 3 days of `order_date`. Late changes to older orders are never picked up, so it is out of sync with `orders` in both directions.

## 2. Fix

1. Remove ids 114-125 from the payments feed. If they come from a real upstream double-load, fix it there too, and confirm with finance first that they are not genuine repeat charges. In this data no other row repeats an `(order_id, payment_method, amount)` combination, which supports them being dupes.
2. Rebuild everything, including `daily_revenue`:
   ```
   dbt build --full-refresh
   ```
   A normal incremental run will not repair the old dates.
3. Optionally, as a safeguard in `stg_payments.sql`, dedupe on the business key. Only do this if repeated identical payments are never legitimate:
   ```sql
   select * from renamed
   qualify row_number() over (
       partition by order_id, payment_method, amount order by payment_id
   ) = 1
   ```
4. For `daily_revenue`, either use a lookback based on a load or updated timestamp rather than `order_date`, or accept periodic full refreshes.

After the fix, `sum(orders.amount)` should be 1672.00, and order 1 should be 10.00.

## 3. Tests that would have caught it

**Business-key uniqueness** on `stg_payments` (this fails on the current data):
```yaml
- name: stg_payments
  tests:
    - dbt_utils.unique_combination_of_columns:
        arguments:
          combination_of_columns: [order_id, payment_method, amount]
        config:
          severity: warn   # a legitimate repeat payment is possible; warn and review
```

**Reconciliation to the processor.** Load the settlement report as a seed, e.g. `raw_processor_settlements(order_id, amount)`, then add `tests/assert_orders_match_settlement.sql`:
```sql
select o.order_id, o.amount, s.amount as settled
from {{ ref('orders') }} o
join {{ ref('raw_processor_settlements') }} s using (order_id)
where abs(o.amount - s.amount) > 0.005
```

**Rollup consistency**, which would catch the stale `daily_revenue` (`tests/assert_daily_revenue_matches_orders.sql`):
```sql
select
    (select round(sum(amount), 2) from {{ ref('orders') }}) as orders_total,
    (select round(sum(revenue), 2) from {{ ref('daily_revenue') }}) as daily_total
where orders_total <> daily_total
```

I haven't run any of this, since only the pasted files were available. The dupe list and the $180 total come from reading `raw_payments.csv` directly.
