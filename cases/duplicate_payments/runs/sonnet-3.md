claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 21 s | USD 0.086 | 2026-10-14 | project 7d5b525+dirty

## 1. Root cause

`seeds/raw_payments.csv` contains 12 duplicate payment rows, ids 114-125. Each repeats an earlier payment with the same `order_id`, `payment_method` and `amount` under a new `id`:

| dup id | order | method | amount | original id |
|---|---|---|---|---|
| 114 | 1 | credit_card | 10.00 | 1 |
| 115 | 10 | bank_transfer | 26.00 | 11 |
| 116 | 18 | credit_card | 8.00 | 21 |
| 117 | 26 | credit_card | 30.00 | 31 |
| 118 | 36 | bank_transfer | 9.00 | 41 |
| 119 | 46 | bank_transfer | 8.00 | 51 |
| 120 | 54 | credit_card | 18.00 | 61 |
| 121 | 62 | gift_card | 14.00 | 71 |
| 122 | 71 | credit_card | 5.00 | 81 |
| 123 | 79 | credit_card | 9.00 | 91 |
| 124 | 88 | credit_card | 29.00 | 101 |
| 125 | 97 | bank_transfer | 14.00 | 111 |

They add up to 180.00, which is exactly 1852 − 1672. Order 1 shows 20.00 instead of 10.00 because payments 1 and 114 are both summed.

Every test passed because none checks this:
- `unique` on `payment_id` holds, since the duplicates have new ids.
- `not_null` and `accepted_values` are satisfied by the duplicate rows.
- `orders.amount` has only a `not_null` test, so nothing compares it to the settlement total.

`stg_payments.sql` passes the rows straight through, so the extra 180 flows into `orders`, `customers` (lifetime value), `region_revenue` and `daily_revenue`.

## 2. Fix

Delete lines for ids 114-125 from `seeds/raw_payments.csv`, leaving 113 rows. The settlement total confirms they are duplicates. I'd rather fix the data than dedupe in staging. A rule on `(order_id, method, amount)` would silently drop a legitimate split payment of the same amount, and no such rows exist today.

Then rebuild with a full refresh:

```
dbt build --full-refresh
```

The full refresh is required because `daily_revenue` is incremental. Its `delete+insert` only reprocesses the last 3 days before `max(order_date)`, and the affected orders are all in January and February 2018. Without `--full-refresh`, `daily_revenue` would keep the inflated numbers, and `sum(amount)` should come back as 1672.00.

## 3. Tests that would have caught it

`tests/assert_no_duplicate_payments.sql` (singular test, fails if any rows are returned):

```sql
select order_id, payment_method, amount, count(*) as n
from {{ ref('stg_payments') }}
group by 1, 2, 3
having count(*) > 1
```

This test would currently fail. If same-amount split payments become legitimate later, change it to `severity: warn` or add a source-side transaction id to the grouping.

A reconciliation test against the processor's settlement total (a small seed or source `settlement_totals`) would catch the same problem even if the duplicates looked different:

```sql
select o.total, s.total
from (select sum(amount) as total from {{ ref('orders') }}) o
cross join (select sum(amount) / 100 as total from {{ ref('raw_settlements') }}) s
where abs(o.total - s.total) > 0.005
```

`raw_settlements` here is a hypothetical seed holding the processor's settlement amounts in cents. Adjust the name and divisor to your feed.

One unrelated issue: `schema.yml` documents `customers.total_order_amount`, but the model outputs `customer_lifetime_value`, so that doc entry is stale.
