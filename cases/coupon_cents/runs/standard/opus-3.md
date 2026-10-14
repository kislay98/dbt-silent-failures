claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 43 s | USD 0.207 | 2026-10-14 | project ad0b16b | prompt 0f146dda0451

# Coupon revenue mismatch: diagnosis, fix, and test

## 1. Root cause

The bug is in the data, not the SQL. Some **coupon rows in `raw_payments` were loaded in dollars instead of cents**, starting in late March 2018. `models/staging/stg_payments.sql` divides every amount by 100, so those rows shrink 100×.

You can see it in the `raw_payments` sample:

| id | order_id | method | amount | expected |
|---|---|---|---|---|
| 27 | 24 | coupon | 2600 | correct, in cents |
| 86 | 76 | coupon | **2** | $2.00 loaded as 2 cents |
| 98 | 86 | coupon | **23** | $23.00 loaded as 23 cents |

Order 94 (2018‑04‑03) in `orders` shows the same thing: `coupon_amount = 0.07` where it should be 7.00.

The figures reconcile exactly. If X dollars were loaded as cents, the shortfall is 0.99·X:

- **March:** 45.00 − 18.27 = 26.73, so X = 27. The 27 dollars appear as 0.27. Corrected coupons are 18 + 27 = 45. Corrected total is 595.27 − 0.27 + 27 = **622.00**, which matches the processor settlement.
- **April:** all 33 coupon dollars appear as 0.33. Corrected total is 106.33 − 0.33 + 33 = 139.00.

Every model reading `stg_payments` is affected: `orders`, `customers` (lifetime values), and `region_revenue` (e.g. NSW 312.02, QLD 282.56, TAS 128.02).

**Why `daily_revenue` looks right (622 / 139):** it is incremental and rebuilds only the last 3 days. The affected dates fell outside that window, so it still holds values from before the bad load. It is stale, and that is the only reason it disagrees with `orders`. The schema tests are all `not_null`, `unique`, and `accepted_values`. A 100× scale error passes every one of them.

## 2. Fix

Fix the ingestion job's coupon feed so it emits cents, then correct the rows already loaded. Don't add a heuristic like `case when amount < 100` in the staging model.

First, find the affected rows and check them against marketing's redemption ledger:

```sql
select p.id, p.order_id, o.ordered_at, p.amount
from raw_payments p join raw_orders o on o.id = p.order_id
where p.payment_method = 'coupon'
order by p.id;
```

The bad rows will be the non-multiples of 100 from late March onward, such as 2, 23, and 7. Confirm that the March bad rows sum to 27 and the April rows to 33.

Then backfill those rows and rebuild:

```sql
update raw_payments
set amount = amount * 100
where payment_method = 'coupon'
  and id in (/* ids confirmed above */);
```

```bash
dbt build --full-refresh
```

The `--full-refresh` makes `daily_revenue` recompute all dates rather than keep stale rows. Afterwards, `orders` should show March coupon 45.00 and amount 622.00, and April coupon 33.00 and amount 139.00.

## 3. Tests that would have caught it

**a) Unit sanity on the raw feed.** This catches the actual defect. In this dataset every legitimate payment is a whole dollar amount stored in cents. Add `tests/assert_raw_payments_in_whole_cents_of_dollars.sql`:

```sql
-- amounts are stored in cents; anything not a whole-dollar multiple suggests a dollars-vs-cents load
select id, order_id, payment_method, amount
from {{ ref('raw_payments') }}
where amount % 100 != 0
```

If real fractional-dollar amounts are possible, use a weaker check that is still useful. For example, test `amount >= 100` per payment method with `severity: warn`, or compare each method's monthly median against the prior month.

**b) Consistency between models.** This test would have failed on this exact build, because the stale `daily_revenue` disagrees with `orders`. Add `tests/assert_daily_revenue_matches_orders.sql`:

```sql
with o as (
    select order_date, count(*) as orders, sum(amount) as revenue
    from {{ ref('orders') }}
    group by order_date
)

select coalesce(o.order_date, d.order_date) as order_date,
       o.revenue as orders_revenue, d.revenue as daily_revenue
from o
full outer join {{ ref('daily_revenue') }} d
    on o.order_date = d.order_date
where o.order_date is null
   or d.order_date is null
   or o.orders != d.orders
   or abs(coalesce(o.revenue, 0) - coalesce(d.revenue, 0)) > 0.005
```

Test (b) only caught this by accident: after a full refresh on bad data, both models would agree. Test (a) is the one that guards the real failure.

The strongest guard would be a reconciliation test against the processor settlement and the coupon redemption ledger, if they can be loaded as sources. Monthly `sum(amount)` by payment method should match within a cent.
