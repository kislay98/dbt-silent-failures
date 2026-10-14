claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 35 s | USD 0.142 | 2026-10-14 | project f769fdf | prompt 2bea65134e32

# Diagnosis: NULL `amount` values in `raw_payments`

## 1. Root cause

**The problem is in the data, not the SQL.** Some rows in `raw_payments` have `amount = NULL`. The ingestion job loads that table; dbt does not. You can see the NULLs in the `stg_payments` sample:

| payment_id | order_id | method | amount |
|---|---|---|---|
| 15 | 13 | bank_transfer | NULL |
| 30 | 25 | coupon | NULL |
| 62 | 54 | bank_transfer | NULL |
| 67 | 58 | gift_card | NULL |

`stg_payments.sql` passes the NULL through (`NULL / 100` = NULL). Then `models/orders.sql` loses it without any error:

- `sum(amount) as total_amount`: `SUM` skips NULLs. Order 25 becomes 20 + 22 = **42** instead of 58. Payment 30, a 16.00 coupon, is missing.
- `sum(case when payment_method = 'coupon' then amount else 0 end)`: the CASE returns NULL for payment 30 and 0 for the other rows, so `coupon_amount` = **0.0**.

**Why `daily_revenue` still matches the settlement:** it is incremental and rebuilds only the last 3 days. The Jan–Mar rows were built before the NULLs appeared in `raw_payments`, so they still hold the correct totals. The monthly differences account for the whole gap:

| month | daily_revenue − orders.amount |
|---|---|
| Jan | 496 − 458 = 38 |
| Feb | 415 − 394 = 21 |
| Mar | 622 − 616 = 6 |
| **Total** | **65 = 1533 − 1468** ✔ |

April matches (139 = 139) because those dates fall inside the 3-day rebuild window. This also means `amount` was fine at some point and the ingestion job broke it later.

**Why every test passed:**
- Nothing tests `stg_payments.amount` or the raw source for nulls.
- `not_null_orders_amount` and the `*_amount` not_null tests cannot catch this. `SUM` returns a non-null value whenever at least one payment is non-null, and the per-method columns fall back to `0`.

`customers.customer_lifetime_value` and `completed_lifetime_value` are understated for the same reason.

## 2. Fix

**Primary fix (data):** reload the affected `raw_payments` rows with their real amounts from the ingestion source. First, find the scope:

```sql
select * from raw_payments where amount is null order by order_id;
```

The NULLs fall on the second and third payments of orders. That points to the ingestion side, for example:
- a source format change, such as amounts arriving as `"16.00"`, that fails to cast into the `INTEGER` column; or
- a dedupe/merge step that nulls the amount.

Check the loader around the time the incremental table stopped agreeing. The missing values total 65.00 for Jan–Mar, and order 25 needs 1600 cents, so you can reconcile the reload against the settlement file.

Do **not** "fix" this with `coalesce(amount, 0)`. That would make the wrong totals permanent and hide the problem.

After the reload, rebuild everything so the incremental table is consistent too:

```bash
dbt build --full-refresh -s stg_payments+
```

**Hardening (code):** make the pipeline fail loudly instead of under-counting. Add tests at the staging layer (section 3). Optionally, also declare `raw_payments` as a `source` and test it there, so the problem is caught before any model runs.

## 3. Tests that would have caught this

**a) Not-null on the payment amount**, in `models/staging/schema.yml`:

```yaml
  - name: stg_payments
    columns:
      - name: payment_id
        tests:
          - unique
          - not_null
      - name: amount
        tests:
          - not_null
      - name: payment_method
        tests:
          - accepted_values:
              arguments:
                values: ['credit_card', 'coupon', 'bank_transfer', 'gift_card']
```

**b) Reconciliation between `daily_revenue` and `orders`**, in `tests/assert_daily_revenue_matches_orders.sql`. This catches any drift between the incremental table and its upstream, whatever the cause:

```sql
with o as (
    select order_date, sum(amount) as revenue
    from {{ ref('orders') }}
    group by order_date
)

select
    coalesce(o.order_date, d.order_date) as order_date,
    o.revenue as orders_revenue,
    d.revenue as daily_revenue
from o
full outer join {{ ref('daily_revenue') }} d
    on o.order_date = d.order_date
where abs(coalesce(o.revenue, 0) - coalesce(d.revenue, 0)) > 0.005
```

On the current data, **(a)** fails on payments 15, 30, 62 and 67. **(b)** fails on every Jan–Mar date that contains one of those orders.

## Side notes (not the cause here)

- **Seed tests are not running.** The log shows 29 tests found but only 27 run, and no seeds ran. So the tests on `raw_customer_regions` are not executing. That table has an `updated_at` column, which suggests it can hold several rows per customer, and that would inflate `region_revenue.revenue`. Consider adding a `unique` test on `customer_id`, or dedupe to the latest row per customer.
- **Column name mismatch.** `schema.yml` documents `customers.total_order_amount`, but the model outputs `customer_lifetime_value`.
