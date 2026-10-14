claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 42 s | USD 0.149 | 2026-10-14 | project f769fdf | prompt a489473ed9d8

# Bank transfer revenue: root cause, fix, and test

## 1. Root cause

**The fault is in the data in `raw_payments`, not in the model SQL.** From order 73 (2018‑03‑16) onward, `bank_transfer` rows in `raw_payments` hold amounts 10,000× too large. `stg_payments.sql` divides every row by 100 because it assumes cents. These rows appear to be in millionths of a dollar instead: order 73 is stored as `3000000` where it should be `300`.

The numbers match the processor statement exactly:

| | Correct-scale rows | Inflated rows | Corrected total |
|---|---|---|---|
| March | 2 + 16 + 26 + 4 = 48 | 1,140,000 / 10,000 = 114 | **162.00** ✓ |
| April | none | 390,000 / 10,000 = 39 | **39.00** ✓ |

Only bank transfers are affected. In the same orders, coupon (order 92: 2.0) and credit card (order 67: 19.0) amounts are normal. Payment 76 (order 67, 2018‑03‑11) is the last correct bank transfer and payment 83 (order 73) is the first bad one. So the bank-transfer feed changed units around 2018‑03‑12 to 2018‑03‑16. The column type is still `INTEGER`, so nothing broke structurally.

**Why every test passed:** all tests on amounts are `not_null`. Nothing checks magnitude or reconciles against the processor.

I haven't seen the raw rows, so please confirm the boundary first:

```sql
select id, order_id, payment_method, amount, amount % 10000 = 0 as divisible
from raw_payments
where payment_method = 'bank_transfer' and id >= 70
order by id;
```

## 2. Fix

**Preferred: fix the data at the source.** Have the ingestion job convert the bank-transfer feed to cents, then correct the rows already loaded. Run this after the query above confirms which rows are affected and that every one is divisible by 10,000:

```sql
update raw_payments
set amount = amount // 10000
where payment_method = 'bank_transfer'
  and id >= 83                -- first bad payment; adjust if the check above says otherwise
  and amount % 10000 = 0;
```

If `raw_payments` is actually loaded from `seeds/raw_payments.csv`, make the same change in the CSV instead, or the next seed load will reintroduce the bad values.

**If the source can't be fixed soon**, normalise in `stg_payments.sql` using an explicit cutover, not a magnitude heuristic:

```sql
        -- `amount` is stored in cents, except bank transfers from payment 83 onward,
        -- which the processor sends in millionths of a dollar
        case
            when payment_method = 'bank_transfer' and id >= {{ var('bank_transfer_micros_from_id', 83) }}
                then amount / 1000000
            else amount / 100
        end as amount
```

**Then rebuild with a full refresh. This step matters.** `daily_revenue` is incremental and only rebuilds the last 3 days. A normal `dbt build` will leave the inflated March and April days in place.

```
dbt build --full-refresh -s daily_revenue+ 
dbt build
```

`orders`, `customers` and `region_revenue` are full tables, so a normal build corrects them. `customers.customer_lifetime_value` and `region_revenue.revenue` are currently inflated too.

## 3. Test that would have caught it

Add a magnitude check on staging, which runs before anything downstream is built. Save it as `tests/assert_stg_payments_amount_plausible.sql`:

```sql
-- Payments are small retail amounts in dollars; anything outside this range is a unit/scale error
select payment_id, order_id, payment_method, amount
from {{ ref('stg_payments') }}
where amount < 0
   or amount > 1000
```

This would have failed on the night payment 83 arrived, and `dbt build` would have skipped `orders` and everything downstream. Choose the ceiling from your real maximum order size. If you use `dbt_utils`, `accepted_range` (`min_value: 0, max_value: 1000`) on `stg_payments.amount` does the same thing.

For stronger protection, add a monthly reconciliation test that compares `sum(amount)` per payment method in `orders` against a processor-statement source. That is the check finance effectively did by hand.
