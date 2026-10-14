claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 33 s | USD 0.146 | 2026-10-14 | project f769fdf | prompt f5f38ede3f4d

# Coupon revenue mismatch: root cause, fix and test

## 1. Root cause

**The data in `raw_payments` is wrong, not the model SQL.** Starting around 2018‑03‑20, coupon payments were loaded into `raw_payments.amount` in **dollars** instead of **cents**. `models/staging/stg_payments.sql` divides every amount by 100 (`amount / 100 as amount`), so these coupons shrank by 100×.

The numbers confirm this exactly:

| order | date | coupon_amount (model) | actual |
|---|---|---|---|
| 58 | 2018‑03‑01 | 18.00 | 18 (loaded correctly) |
| 76 | 2018‑03‑20 | 0.02 | 2 |
| 81 | 2018‑03‑23 | 0.02 | 2 |
| 86 | 2018‑03‑26 | 0.23 | 23 |
| 92 | 2018‑04‑02 | 0.02 | 2 |
| 94 | 2018‑04‑03 | 0.07 | 7 |
| 95 | 2018‑04‑04 | 0.24 | 24 |

- **March coupons:** 18 + 2 + 2 + 23 = **45.00**, which matches the ledger. The model shows 18.27.
- **April coupons:** 2 + 7 + 24 = **33.00**, which matches the ledger. The model shows 0.33.
- **March settlement:** 595.27 − 0.27 + 27 = **622.00**, which matches the processor exactly. So in March, nothing besides these three coupons is off.
- Order 92 (`amount` 15.02) shows the problem is per payment row: its non‑coupon payment of 1500 cents is correct, while its coupon row of `2` is in dollars.

All tests passed because nothing checks magnitude. `not_null`, `unique` and `accepted_values` are all satisfied by a 0.02 coupon. `raw_payments.amount` is an INTEGER, so dollars and cents look identical in type.

## 2. Fix

**Fix the ingestion job first.** Whatever loads coupon payments (likely a changed coupon/redemption feed or mapping around mid‑March 2018) must emit cents again. Then backfill the bad rows.

Before changing anything, run this to see the bad rows:

```sql
select p.*, o.ordered_at
from raw_payments p
join raw_orders o on o.id = p.order_id
where p.payment_method = 'coupon'
  and o.ordered_at >= '2018-03-02'
order by o.ordered_at;
```

If that returns only payments for orders 76, 81, 86, 92, 94 and 95, apply the data fix:

```sql
update raw_payments
set amount = amount * 100
where payment_method = 'coupon'
  and order_id in (76, 81, 86, 92, 94, 95);
```

Then rebuild. **`daily_revenue` needs a full refresh.** It is incremental and only rebuilds the last 3 days, so the March and early‑April dates would otherwise keep the wrong revenue:

```sh
dbt build --full-refresh --select stg_payments+
```

Expected after the fix: coupons are 45.00 in March and 33.00 in April. March `amount` is 622.00, and April rises from 106.33 to 139.00.

I'd keep the correction out of `stg_payments.sql`. A rule like "multiply small coupons by 100" would silently corrupt any legitimate small coupon and hide the next ingestion regression.

## 3. Test that would have caught this

Add a singular test, `tests/assert_payment_amounts_plausible.sql`:

```sql
-- raw amounts are cents; a payment under $1 almost always means a unit error upstream
select
    payment_id,
    order_id,
    payment_method,
    amount
from {{ ref('stg_payments') }}
where amount < 1
```

This would have failed on six rows. If sub‑dollar payments can be legitimate, use a lower threshold or `config(severity='warn')`.

The stronger guard is a reconciliation test. If the redemption ledger or processor settlement can be loaded as a source, compare monthly `sum(coupon_amount)` and `sum(amount)` against them. That catches any unit, duplication or missing‑row error, not just this one.

## Side notes from the same files

- **`raw_customer_regions` has an `updated_at` column**, which suggests multiple rows per customer. That would fan out the join in `region_revenue`, and there is no `unique` test on `customer_id`. Its `not_null` tests also didn't run in this build: 29 tests were found but only 27 ran, because seeds were excluded.
- **`models/schema.yml` documents `total_order_amount`** on `customers`, but the model outputs `customer_lifetime_value`.
