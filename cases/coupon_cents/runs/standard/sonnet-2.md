claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 40 s | USD 0.110 | 2026-10-14 | project ad0b16b | prompt 0f146dda0451

## 1. Root cause

**File: `models/staging/stg_payments.sql`, with bad rows in `raw_payments`.** Every raw amount is divided by 100 (`amount / 100 as amount`), on the assumption that the feed is always in cents. Some coupon rows in `raw_payments` for March and April were loaded in whole dollars. Examples visible in the sample: id 98 (order 86, `23`), id 86 (order 76, `2`), and order 94, which shows `0.07`. Those rows become `0.23`, `0.02` and `0.07` instead of `23.00`, `2.00` and `7.00`.

I haven't run anything. This comes from the arithmetic:

| | Ledger | `orders.coupon_amount` | Gap |
|---|---|---|---|
| March | 45.00 | 18.27 | 26.73 |
| April | 33.00 | 0.33 | 32.67 |

- **March:** the misscaled rows sum to X raw units and the correct ones to Y dollars. Y + X = 45 and Y + X/100 = 18.27, so X = 27 and Y = 18. The same solve for April gives X = 33 and Y = 0.
- **Settlement:** 622.00 − 595.27 = 26.73, the same gap as the March coupon difference, so coupons are the only affected method.
- **Fingerprint:** correctly loaded cents are all multiples of 100 (2000, 200, 800, 2600). The bad rows (23, 2, 7) are not.

Three things made this invisible:
- **Tests:** every test is `not_null`, `unique` or `accepted_values`, and none of them checks magnitudes or reconciles against anything.
- **`daily_revenue`:** the incremental model only rebuilds the last 3 days, so older days keep values from before the bad load. That is why `daily_revenue` shows 622.00 for March (matching settlement) and 139.00 for April while `orders` shows 595.27 and 106.33. It is correct only by accident.
- **Jan/Feb:** those months were loaded correctly, which is why they agree.

## 2. Fix

1. **Identify the rows** (a candidate list; confirm against the processor or ledger before changing anything):
   ```sql
   select id, order_id, amount from raw_payments
   where payment_method = 'coupon' and amount % 100 <> 0;
   ```
   The raw units should sum to 27 for March orders and 33 for April.
2. **Fix the ingestion job** so coupon amounts are loaded in cents, then reload or patch the confirmed ids:
   ```sql
   update raw_payments set amount = amount * 100 where id in (/* confirmed ids */);
   ```
   I wouldn't add a `case when amount % 100 <> 0` heuristic to `stg_payments`. It would silently misread any genuine sub-dollar payment.
3. **Rebuild with a full refresh** so the stale incremental rows are replaced:
   ```
   dbt build --full-refresh -s daily_revenue
   dbt build
   ```
4. **Check the result.** March coupon should be 45.00, April 33.00, March `sum(amount)` 622.00 and April 139.00, and `daily_revenue` should match `orders` for every month.

Separately, the 3-day lookback in `daily_revenue` will never pick up corrections to older dates. Either widen it or run periodic full refreshes.

## 3. Tests

**Unit check on the raw feed** (this would have caught the root cause at load time). `tests/assert_payments_in_cents.sql`:
```sql
select id, payment_method, amount
from {{ ref('raw_payments') }}
where amount % 100 <> 0
```
Set `config(severity='warn')` if the feed legitimately has odd cents.

**Reconciliation of `daily_revenue` to `orders`** (this would have caught the stale incremental rows). `tests/assert_daily_revenue_matches_orders.sql`:
```sql
with o as (
    select order_date, count(*) as orders, round(sum(amount), 2) as revenue
    from {{ ref('orders') }} group by 1
)
select coalesce(o.order_date, d.order_date) as order_date, o.revenue as orders_revenue, d.revenue as daily_revenue
from o
full outer join {{ ref('daily_revenue') }} d on o.order_date = d.order_date
where o.orders is distinct from d.orders
   or abs(coalesce(o.revenue, 0) - coalesce(d.revenue, 0)) > 0.005
```

**Payments to orders reconciliation** (a guard against future fan-out or drops):
```sql
select 1
from (select sum(amount) a from {{ ref('stg_payments') }}) p,
     (select sum(amount) a from {{ ref('orders') }}) o
where abs(p.a - o.a) > 0.005
```

If you can export monthly settlement and ledger totals as a seed, add a per-month, per-method comparison against `orders`. That is the check that would have flagged the coupon gap directly.
