claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 116 s | USD 0.016 | 2026-10-14 | project 8ee5734 | prompt d0c0d63b3bb2

## 1. Root cause

**The warehouse counts only `status = 'completed'`, but the source data also contains `delivered` orders, and the models ignore them.**

- **The gap is exactly the delivered orders.** Finance's figures and the warehouse differ by 136.00 in March (279.00 − 143.00) and by 136.00 since January (1103.00 − 967.00). The output sample has no `delivered` orders in January or February. In March there are 8 delivered orders totalling 134.02, which explains most of the gap.
- **The customer sample confirms it.** Customer 8 has order 70 delivered at 26.0, and lifetime 45.0 − 26.0 = completed 19.0. Customers 19 and 36 have delivered orders (3.0 and 28.0) and `completed_lifetime_value` of 0.0.
- **Where it's wrong:** `jaffle_shop/models/customers.sql`, in the `customer_payments` CTE:
  `sum(case when orders.status = 'completed' then amount else 0 end) as completed_amount`
  This is the only place in the models that defines completed revenue.
- **What's wrong in the data:** `raw_orders.status` now contains `delivered`, which first appears in March 2018. The source system added a status and nothing downstream was updated to classify it.
- **Why every test passed:** `delivered` was added to the `accepted_values` lists in `jaffle_shop/models/schema.yml` and `jaffle_shop/models/staging/schema.yml`. That makes the status test check "is this a known status" rather than "is it counted correctly." No test ties completed revenue to anything outside the warehouse.

**One residual I can't explain from these samples:** completed + delivered in March is 143.00 + 134.02 = 277.02, which leaves 1.98 of the admin's 279.00 unexplained. The same 1.98 appears in the since-January figure, so it is a March item. It might be a delivered order the admin prices differently, or a status the admin treats differently. Pulling the admin's March order list will settle it.

## 2. Fix

Assuming finance confirms that `delivered` counts as completed, change the one place that defines it, in `jaffle_shop/models/customers.sql`:

```sql
sum(case when orders.status in ('completed', 'delivered') then amount else 0 end) as completed_amount
```

This should bring `completed_lifetime_value` to 967.00 + 134.02 = 1101.02. It will not match 1103.00 until the 1.98 residual is explained. If finance decides `delivered` should not count, the admin is the thing that's wrong, and the fix goes the other way.

Also update the `description` for `status` in `jaffle_shop/models/schema.yml`, so the mapping from status to revenue treatment is documented next to the accepted values.

A latent risk to fix at the same time: `raw_payment_methods` (the seed joined in `orders.sql`) has `not_null` tests but no `unique` test on `payment_method`. If the feed ever has more than one row per method, every payment fans out and `orders.amount` is inflated. I didn't find evidence of this in the current numbers, since the orders and customers completed totals agree at 967.00, but add the test:

```yaml
  - name: raw_payment_methods
    columns:
      - name: payment_method
        tests:
          - unique
          - not_null
```

## 3. Test that would have caught it

Reconcile the warehouse against finance's independent number. Add a seed with the admin's completed totals (`seeds/raw_admin_completed_orders.csv`, one row per order_id with its admin amount), then a singular test:

```sql
-- tests/completed_revenue_matches_admin.sql
with warehouse as (
    select round(sum(completed_lifetime_value), 2) as total
    from {{ ref('customers') }}
),

admin as (
    select round(sum(amount), 2) as total
    from {{ ref('raw_admin_completed_orders') }}
)

select warehouse.total as warehouse_total, admin.total as admin_total
from warehouse
cross join admin
where warehouse.total <> admin.total
```

This test fails on the current code (967.00 vs 1103.00) and will keep failing until the 1.98 residual is resolved. Because it compares against an external source, it catches any status the models don't classify, not just `delivered`.

I haven't run `dbt build` or any queries. All of the above is derived from the log, the models, and the output samples you pasted.
