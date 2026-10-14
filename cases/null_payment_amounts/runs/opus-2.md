claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 31 s | USD 0.129 | 2026-10-14 | project f769fdf | prompt 2bea65134e32

## 1. Root cause

**Some `amount` values in `raw_payments` are NULL.** The ingestion job loaded them that way. No model file is wrong. `models/orders.sql` passes the NULLs through silently.

**How the NULLs disappear:**

- `stg_payments.sql` computes `amount / 100`. When `amount` is NULL, the result is NULL.
- `orders.sql` aggregates with `sum(...)`, and SQL `sum` skips NULLs. An order with one NULL payment and one valid payment gets the partial total, not NULL.
- Order 25 has payments of 20 (bank transfer), 22 (credit card) and NULL (coupon, payment 30). It shows `42.00`. The missing 16.00 is the coupon payment.

**The numbers reconcile with this:**

| month | `orders.amount` | `daily_revenue` | missing |
|---|---|---|---|
| Jan | 458 | 496 | 38 |
| Feb | 394 | 415 | 21 |
| Mar | 616 | 622 | 6 |
| **Total** | **1468** | **1533** | **65** = 1533 − 1468 ✔ |

**Why `daily_revenue` still matches the settlement:** it is incremental, and each run only rebuilds `order_date >= max(order_date) - 3 days`. The Jan–Mar rows were computed before the bad load and are never recomputed. That is luck, not correctness. The rebuilt days will pick up NULLs, and a `--full-refresh` would make the dashboard wrong too.

**Why every test passed:**

- Nothing tests `stg_payments.amount` or `raw_payments.amount` for NULLs.
- `not_null_orders_amount` cannot fail here. `sum()` ignores NULLs, and the per-method columns use `case ... else 0`, so they never turn NULL.

**Other affected models:** `customers` (lifetime values) and `region_revenue` are understated the same way.

**Pattern in the sample:** every NULL is a second or later payment on a split-payment order (payments 15, 30, 62, 67). That points at how the ingestion job handles split payments. Check that job's change history or load errors, for example a parse or type-cast failure that wrote NULL instead of rejecting the row.

## 2. Fix

**Data (the actual fix):** fix the ingestion job and re-load `raw_payments` from the processor. The missing amounts can't be reconstructed in dbt. We only know the per-month totals, not the per-payment values, and `coalesce(amount, 0)` would just hide the problem. After re-loading, rebuild everything, including the incremental model:

```bash
dbt build --full-refresh
```

**Code (so this fails loudly next time):** in `models/orders.sql`, make an order's total NULL when any of its payments has no amount. Then the existing `not_null_orders_amount` test catches it:

```sql
        -- an order with any payment missing its amount has an unknown total, not a partial one
        case when count(*) = count(amount) then sum(amount) end as total_amount
```

## 3. Tests that would have caught it

In `models/staging/schema.yml`, add a not-null test on the payment amount. Ideally also declare `raw_payments` as a source and test it there, so the failure is reported at the ingestion boundary.

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
```

Optionally, add a reconciliation test, e.g. `tests/assert_daily_revenue_matches_orders.sql`. It flags any drift between the incremental table and its source:

```sql
select o.order_date, o.amount, d.revenue
from (select order_date, sum(amount) as amount from {{ ref('orders') }} group by 1) o
full outer join {{ ref('daily_revenue') }} d using (order_date)
where abs(coalesce(o.amount, 0) - coalesce(d.revenue, 0)) > 0.005
```

**Unrelated issue I noticed:** `raw_customer_regions` has an `updated_at` column, which suggests it may hold more than one row per customer. If so, the join in `region_revenue` would multiply revenue. A `unique` test on its `customer_id` would confirm the "one row per customer" description.
