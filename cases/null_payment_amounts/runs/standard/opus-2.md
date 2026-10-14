claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 30 s | USD 0.182 | 2026-10-14 | project ad0b16b | prompt b1e95fc444b5

## 1. Root cause

**The bad data is in `raw_payments.amount`. Some payment rows have `amount = NULL`.** The model code is not the cause.

You can see this in the sample:

```
raw_payments:  id 30 → order 25, coupon,      amount NULL
               id 55 → order 49, credit_card, amount NULL
```

Here is how the NULLs flow through the project without any test noticing:

- **`models/staging/stg_payments.sql`**: `amount / 100` passes the NULL through. No test covers `stg_payments.amount`. There is no `raw_payments` entry in `schema.yml` either, so the raw table is untested too.
- **`models/orders.sql`**: `sum(payments.amount)` and the `sum(case when ... then payments.amount ...)` columns skip NULLs. Every other order has non-NULL payments, so `amount`, `coupon_amount` and the rest come out as smaller numbers rather than NULL. The `not_null` tests pass, and the totals are just low.
  - Order 25: bank transfer 20.00 plus other payments = **42.00**. The 16.00 coupon payment (id 30) is NULL and dropped, so it shows 42.00 instead of **58.00**.
- **`models/daily_revenue.sql`** still matches the settlement only because it is incremental. Each run rebuilds just the last 3 days (`order_date >= max(order_date) - 3`). The Jan–Mar rows were built before the amounts became NULL and have not been recomputed since.

| month | daily_revenue (old, correct) | orders (current) | missing |
|---|---|---|---|
| Jan | 496 | 458 | 38 |
| Feb | 415 | 394 | 21 |
| Mar | 622 | 616 | 6 |
| **Total** | **1533** | **1468** | **65** |

April agrees (139 = 139), so the NULLs only affect Jan–Mar payments. `customers.customer_lifetime_value` (1607 = 1468 + 139) and `region_revenue` are understated by the same amount.

**Important:** do **not** run `dbt build --full-refresh` on `daily_revenue` until the data is fixed. Right now it holds the only copy of the correct totals in the warehouse, and a full refresh would replace them with the wrong numbers.

## 2. Fix

This needs a data fix. The original amounts can't be derived in SQL, and `coalesce(amount, 0)` would only hide the problem.

1. Find the affected rows:
   ```sql
   select p.*, o.ordered_at
   from raw_payments p join raw_orders o on o.id = p.order_id
   where p.amount is null
   order by p.id;
   ```
   The missing amounts should add up to 6500 cents, with 1600 cents for id 30.
2. Re-run the ingestion of `raw_payments` from the processor feed for Jan–Mar 2018, or patch the rows from the settlement file (for example, `update raw_payments set amount = 1600 where id = 30;`). Also find out why the ingestion job started writing NULL amounts, for example a column rename or type change in the feed.
3. Rebuild: `dbt build` (or `dbt build --full-refresh -s daily_revenue+` to recompute every day). Check that `orders.amount` sums to 1533.00 for Jan–Mar and that order 25 shows 58.00.

## 3. Tests that would have caught it

Fail on NULL payment amounts at the staging layer, in `models/staging/schema.yml`:

```yaml
  - name: stg_payments
    columns:
      - name: payment_id
        tests: [unique, not_null]
      - name: amount
        tests:
          - not_null
      # ...payment_method accepted_values as before
```

Ideally, also declare `raw_payments` as a source with the same `not_null` test on `amount`, so the problem is caught at ingestion.

Then add a reconciliation test between the incremental and full models. It catches any divergence like this one, whatever the cause. Create `tests/daily_revenue_matches_orders.sql`:

```sql
-- daily_revenue is incremental; it must still agree with a full recompute from orders
with expected as (
    select order_date, count(*) as orders, sum(amount) as revenue
    from {{ ref('orders') }}
    group by order_date
)

select
    coalesce(e.order_date, d.order_date) as order_date,
    e.revenue as expected_revenue,
    d.revenue as actual_revenue
from expected e
full outer join {{ ref('daily_revenue') }} d
    on e.order_date = d.order_date
where e.orders is distinct from d.orders
   or abs(coalesce(e.revenue, 0) - coalesce(d.revenue, 0)) > 0.005
```

With the current data, both tests would have failed: `not_null_stg_payments_amount` on ids 30, 55 and the others, and the reconciliation test on every affected Jan–Mar date.
