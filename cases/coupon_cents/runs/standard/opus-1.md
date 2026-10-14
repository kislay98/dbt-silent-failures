claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 38 s | USD 0.200 | 2026-10-14 | project ad0b16b | prompt 0f146dda0451

# 1. Root cause

**The coupon rows in `raw_payments` for March and April orders were loaded in dollars, not cents. `models/staging/stg_payments.sql` divides every amount by 100, so these rows end up 100× too small.**

The dbt code itself is unchanged and correct for its documented contract ("`amount` is currently stored in cents"). The problem is in the data the ingestion job writes to `raw_payments`.

The numbers point to this exactly. Let `c` be the coupon dollars loaded correctly in cents and `x` the coupon dollars loaded in dollars:

| Month | Ledger (`c + x`) | `orders.coupon_amount` (`c + x/100`) | Solve |
|---|---|---|---|
| March | 45.00 | 18.27 | x = 27, c = 18 |
| April | 33.00 | 0.33 | x = 33, c = 0 |

- **March settlement:** 622.00 − 595.27 = 26.73 = 27 × 0.99. The whole revenue gap is the coupon mis-scaling.
- **Visible rows:** `raw_payments` id 98 (order 86, coupon, `23`) and id 86 (order 76, coupon, `2`). Order 94 (2018-04-03) has `coupon_amount = 0.07`. Every other payment is a whole number of dollars ×100. January and February coupon totals (90.0, 17.0) are whole dollars, so only the later rows are affected.
- **Other symptoms:** fractional revenue in `region_revenue` (NSW 312.02, QLD 282.56, TAS 128.02), `customers.customer_lifetime_value` (1612.6), and `placed`/`shipped` status totals.
- **Fees are unaffected** because coupons have `fee_rate = 0`.

**Why `daily_revenue` disagrees with `orders`:** `daily_revenue` shows March 622.00 and April 139.00 (106.33 + 32.67). Those are the correct numbers. The incremental model built those dates before the bad coupon rows were (re)loaded, and it only rebuilds the last three days. So it still holds stale, correct values (e.g. 2018-04-03 = 33.0). A `--full-refresh` today would make it wrong too. The disagreement between two models fed by the same source is what no test checked.

**Why all tests passed:** they only check `not_null`, `unique`, and `accepted_values`. A value of 0.23 is a valid non-null amount, so nothing checked magnitude or reconciliation.

# 2. Fix

**Preferred: fix the data at the source.** Correct the ingestion job (or the coupon feed it reads) so it emits cents like everything else, then repair the rows already loaded. First list the suspect rows. I haven't seen the seed data, so confirm these against the redemption ledger before updating:

```sql
select p.id, p.order_id, p.payment_method, p.amount, o.ordered_at
from raw_payments p
join raw_orders o on o.id = p.order_id
where p.amount % 100 <> 0
order by o.ordered_at;
-- expect coupon rows only, summing to 27 (March) and 33 (April)
```

```sql
update raw_payments
set amount = amount * 100
where payment_method = 'coupon'
  and amount % 100 <> 0;   -- restrict to the ids confirmed above
```

Then rebuild everything, including the incremental model, so all models agree:

```bash
dbt build --full-refresh
```

**Stopgap if the feed can't be fixed yet** (`models/staging/stg_payments.sql`). This only works because every legitimate payment here is a whole number of dollars. Remove it once the source is fixed.

```sql
        -- `amount` is stored in cents, except coupon rows from the new feed which arrive in dollars
        -- TODO: remove once ingestion emits cents for coupons
        case
            when payment_method = 'coupon' and amount % 100 <> 0 then amount
            else amount / 100
        end as amount
```

After either fix, check that March totals 622.00 and coupons total 45.00 / 33.00.

# 3. Tests that would have caught it

**a) Reconciliation between `orders` and `daily_revenue`.** This one would have failed last night, with March 595.27 vs 622.00. It also catches any upstream restatement that lands outside the incremental window.

`tests/assert_daily_revenue_matches_orders.sql`:
```sql
with from_orders as (
    select order_date, count(*) as orders, round(sum(amount), 2) as revenue
    from {{ ref('orders') }}
    group by order_date
),
from_daily as (
    select order_date, orders, round(revenue, 2) as revenue
    from {{ ref('daily_revenue') }}
)
select *
from from_orders
full outer join from_daily using (order_date)
where from_orders.orders  is distinct from from_daily.orders
   or from_orders.revenue is distinct from from_daily.revenue
```

**b) A unit-contract check on the raw source.** This catches the bad load at the point of ingestion. Set it to `warn` if sub-dollar payments could ever be legitimate.

`tests/assert_raw_payments_whole_dollars_in_cents.sql`:
```sql
{{ config(severity='error') }}
select id, order_id, payment_method, amount
from {{ ref('raw_payments') }}
where amount % 100 <> 0
```

If you have a feed of the processor settlement or the redemption ledger, the strongest guard is a monthly reconciliation test of `orders` against it, since that is the check marketing ran by hand.
