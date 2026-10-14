claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 33 s | USD 0.143 | 2026-10-14 | project f769fdf | prompt f5f38ede3f4d

# Root cause: coupon payments were loaded in dollars, then divided by 100 again

## 1. Root cause

The bad data is in **`raw_payments`**, the table the ingestion job loads (`seeds/raw_payments.csv` if it comes from the seed). Starting around 2018‑03‑20, coupon payments were written in **dollars** instead of **cents**. `models/staging/stg_payments.sql` divides every `amount` by 100:

```sql
amount / 100 as amount   -- assumes cents
```

So a $2.00 coupon stored as `2` comes out as `0.02`. The model code is the same for all months. What changed is the unit of the incoming data. Every test passes because none of them checks the size of an amount, only that it is not null or unique.

The numbers match this exactly once the suspect rows are multiplied by 100:

| month | order | current | ×100 | |
|---|---|---|---|---|
| Mar | 58 (03‑01) | 18.00 | 18.00 (correct already) | |
| Mar | 76, 81, 86 | 0.02, 0.02, 0.23 | 2 + 2 + 23 | **18 + 27 = 45.00** ✓ ledger |
| Apr | 92, 94, 95 | 0.02, 0.07, 0.24 | 2 + 7 + 24 | **33.00** ✓ ledger |

- **March settlement:** 595.27 − 0.27 + 27.00 = **622.00** ✓
- **Order 92** (15.02): the 15.00 credit card part is correct and only the coupon part is wrong. So only coupon rows are affected, not all of that day's payments.
- **January and February** coupons (e.g. order 3 = 1.00, i.e. 100 cents) are in cents and correct.

I don't have the seed or raw rows, so this comes from the arithmetic above, not from looking at `raw_payments` directly. Before changing anything, run the query in step 2a to confirm it returns exactly those six payments.

## 2. Fix

**a. Fix the data at the source (preferred).** Get the ingestion job (the coupon/redemption feed) to send cents again, then repair the rows already loaded:

```sql
-- inspect first: expect the coupon payments for orders 76, 81, 86, 92, 94, 95
select p.*, o.ordered_at
from raw_payments p
join raw_orders o on o.id = p.order_id
where p.payment_method = 'coupon'
  and p.amount < 100          -- under $1 in cents: implausible for a coupon
order by o.ordered_at;

-- then repair
update raw_payments
set amount = amount * 100
where payment_method = 'coupon'
  and amount < 100
  and order_id in (76, 81, 86, 92, 94, 95);   -- list taken from the inspect query
```

If the raw data comes from `seeds/raw_payments.csv`, make the same change in the CSV and run `dbt seed -s raw_payments`. Don't add a `case when payment_method = 'coupon' and date >= … then amount * 100` patch to `stg_payments`. It hides the problem and breaks as soon as the feed is fixed.

**b. Rebuild, including a full refresh of `daily_revenue`.** That model is incremental and only reprocesses `max(order_date) - 3 days`. A normal build will never rewrite the March and early‑April rows, so it would keep the wrong revenue even after the raw data is fixed:

```bash
dbt build --full-refresh -s daily_revenue+ 
dbt build
```

`orders`, `customers` and `region_revenue` are full tables and will correct themselves on the next run.

## 3. A test that would have caught this

Add a singular test, `tests/assert_payment_amounts_plausible.sql`:

```sql
-- Raw payment amounts are in cents. A nonzero payment under $1.00 almost always
-- means a row was loaded in dollars and then divided by 100 again.
select
    payment_id,
    order_id,
    payment_method,
    amount
from {{ ref('stg_payments') }}
where amount > 0
  and amount < 1
```

This fails on the six bad rows. You could also add a source‑level test on `raw_payments.amount` (for example `dbt_utils.accepted_range` with `min_value: 100`) so the problem is caught at load time.

A stronger check, if marketing's redemption ledger can be loaded as a source, is a monthly reconciliation test. It would fail whenever `sum(orders.coupon_amount)` by month differs from the ledger by more than a cent. That catches unit errors and also any other way the two drift apart.
