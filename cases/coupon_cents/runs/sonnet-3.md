claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 26 s | USD 0.067 | 2026-10-14 | project bef6b69

## 1. Root cause

The problem is in the data in `raw_payments`, not in the SQL. `models/staging/stg_payments.sql` hard-codes `amount / 100`, on the assumption that `raw_payments.amount` (INTEGER) is always in cents. Some coupon rows arrive in whole dollars instead, and the model shrinks them by 100×.

The arithmetic points to the coupon payments for orders 76, 81, 86 (March) and 92, 94, 95 (April):

| Month | Ledger | `orders.coupon_amount` | Raw values that reconcile |
|---|---|---|---|
| March | 45.00 | 18.27 = 18 + 0.02 + 0.02 + 0.23 | 18 + 2 + 2 + 23 = 45 |
| April | 33.00 | 0.33 = 0.02 + 0.07 + 0.24 | 2 + 7 + 24 = 33 |

The March gap is 45.00 − 18.27 = 26.73. That is exactly the processor gap of 622.00 − 595.27 = 26.73, so coupon rows account for the whole shortfall. Order 58's coupon (18.0, so 1800 cents) is a normal cents row. The coupon feed apparently switched to dollars partway through March (from about order 76, 2018-03-20).

I inferred this from the numbers and haven't looked at the raw rows. Confirm with the query below.

All 40 tests pass because none of them check magnitude or reconcile to an external total. `not_null` and `accepted_values` on `payment_method` say nothing about units, and `orders.coupon_amount` is non-null either way.

## 2. Fix

Correct the data and fix the ingestion job so it stops emitting mixed units. First confirm the affected rows:

```sql
select id, order_id, amount
from raw_payments
where payment_method = 'coupon' and amount < 100
order by order_id;
```

You should see the six rows above, with amounts 2, 2, 23, 2, 7, 24. If so, convert them to cents:

```sql
update raw_payments
set amount = amount * 100
where payment_method = 'coupon'
  and order_id in (76, 81, 86, 92, 94, 95);
```

Then run `dbt build`. March coupons should come to 45.00, April to 33.00, and March `sum(amount)` to 622.00.

Don't add a "if amount < 100, multiply" heuristic to `stg_payments`. It would silently misread genuine sub-dollar payments. If the feed really is mixed, add an explicit unit column or a cutover rule in ingestion.

`daily_revenue` is incremental with a 3-day lookback, so after correcting history run `dbt run -s daily_revenue --full-refresh`. Otherwise old dates keep the wrong revenue.

## 3. Tests

A singular test that reconciles to the ledger would have caught this directly. Load the ledger and processor settlements as a seed or source, for example `ledger_coupon_monthly(month, amount)`:

```sql
-- tests/assert_coupon_revenue_matches_ledger.sql
with ours as (
    select date_trunc('month', order_date) as month,
           round(sum(coupon_amount), 2) as amount
    from {{ ref('orders') }}
    group by 1
)
select o.month, o.amount as ours, l.amount as ledger
from ours o
join {{ ref('ledger_coupon_monthly') }} l using (month)
where abs(o.amount - l.amount) > 0.005
```

A cheaper plausibility test needs no external data and flags sub-dollar payments, which are almost certainly a unit error:

```sql
-- tests/assert_no_subdollar_payments.sql
select payment_id, order_id, payment_method, amount
from {{ ref('stg_payments') }}
where amount > 0 and amount < 1
```

Set it to `severity: warn` if genuine small payments exist.

## Other risks

I haven't confirmed these as causing this issue:

- `raw_customer_regions` has an `updated_at` column, which suggests multiple rows per customer. Only `not_null` is tested on it, and `region_revenue` joins on `customer_id`. Duplicate rows would fan out and inflate revenue. Add a `unique` test on `customer_id`, or take the latest row per customer.
- The 3-day lookback in `daily_revenue` won't pick up payments that land later than that.
