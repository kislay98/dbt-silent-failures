claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 31 s | USD 0.100 | 2026-10-15 | project ad0b16b | prompt 75e42f540dd8

## 1. Root cause

The problem is in the raw data, in `seeds/raw_payments.csv`, loaded by the ingestion job (I can't see the file itself). From roughly mid-March (after order 67, by the sample) every **`bank_transfer`** row has its `amount` inflated by exactly **10,000×**. Other methods are untouched, and earlier bank transfers are fine (order 67: 400 cents, order 57: 200 cents).

- Order 73 shows 30,000.00 and the customer paid 3.00, so the raw amount is 3,000,000 cents where 300 is expected.
- Orders 78, 90 and 98 show 260,000, 20,000 and 100,000, which are 26, 2 and 10 dollars × 10,000.
- April: 390,000 / 39 = 10,000 exactly.
- March: 1,140,048 − 162 = 9,999 × S, so S = 114. That means 114 of the 162 dollars of March bank transfers are scaled and 48 are correct.

`stg_payments.sql` does `amount / 100` and assumes cents, and `orders.sql` sums whatever arrives. The inflation flows into `orders` (amount, bank_transfer_amount, fees), `customers` (lifetime value), `region_revenue` and `daily_revenue`. Presumably the ANZ feed changed units or was double-scaled at ingestion.

`daily_revenue` has a second problem. It disagrees with `orders` (March 622 vs 1,140,508, April 240,115 vs 390,100) because it is incremental and only rebuilds the last 3 days. Rows already built before or after the bad load never get corrected, so it is stale and silently wrong.

Every test passed because they only check null, unique, accepted values and relationships, and nothing checks magnitude or reconciliation.

## 2. Fix

1. Find the cutover (this needs the data). A scaled row is at least 100 cents × 10,000 = 1,000,000:
   ```sql
   select id, order_id, amount from raw_payments
   where payment_method = 'bank_transfer' and amount >= 1000000 order by id;
   ```
   Confirm the rows start at one payment id and that every bank transfer from that id on is in the list.
2. Correct the data in `raw_payments.csv`: divide `amount` by 10,000 for those rows (or have the ingestion job apply the correct unit and reload). The root fix belongs in the ingestion job or ANZ feed config, so this doesn't recur.
3. Rebuild with a full refresh, so `daily_revenue` is recomputed:
   ```
   dbt seed && dbt build --full-refresh
   ```
4. Verify: March bank transfers should be 162.00 and April 39.00, and order 73 should show 3.00.

Until the upstream fix lands, a stopgap in `stg_payments.sql` would be `case when payment_method = 'bank_transfer' and amount >= 1000000 then amount / 10000 else amount end / 100`. I'd avoid keeping that, because it hides a feed defect.

## 3. Tests that would have caught it

`tests/assert_payment_amounts_plausible.sql`, a sanity bound on payment size (any row returned fails):
```sql
select payment_id, order_id, payment_method, amount
from {{ ref('stg_payments') }}
where amount > 10000  -- dollars; pick a bound above the largest real payment
   or amount < 0
```

`tests/assert_daily_revenue_matches_orders.sql`, to catch the stale incremental:
```sql
select d.order_date, d.revenue, o.revenue as expected
from {{ ref('daily_revenue') }} d
join (select order_date, sum(amount) as revenue
      from {{ ref('orders') }} group by 1) o using (order_date)
where abs(d.revenue - o.revenue) > 0.005
```

`tests/assert_orders_amount_equals_methods.sql`, an internal consistency check:
```sql
select order_id from {{ ref('orders') }}
where abs(amount - (credit_card_amount + coupon_amount
                    + bank_transfer_amount + gift_card_amount)) > 0.005
```

The first test would have failed on the first bad load. For a durable control, also reconcile monthly `bank_transfer_amount` against the processor statement totals (162 and 39) as a seed-backed test.
