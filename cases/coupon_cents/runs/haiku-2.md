claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 36 s | USD 0.005 | 2026-10-14 | project bef6b69

## 1. Root cause

**The models are not dropping rows. The March and April coupon payments never reach `orders` because their `order_id` has no matching row in `raw_orders`/`stg_orders`, or is NULL.** The data gap starts at ingestion, and nothing in the project checks for it.

The numbers line up exactly:

- March: ledger 45.00 − `orders` 18.27 = **26.73**, which equals the settlement gap 622.00 − 595.27 = **26.73**. The whole March gap is coupon money.
- April: ledger 33.00 − `orders` 0.33 = **32.67** missing.
- January and February agree, so the problem is in the recent load, not in the logic that has worked before.

The mechanism is in `jaffle_shop/models/orders.sql`. `final` is driven from `orders` (`stg_orders`) and left-joins `order_payments`. Any payment whose `order_id` is absent from `stg_orders`, or is NULL, is aggregated into `order_payments` and then never joined to anything. It disappears silently.

I can't see the orphaned rows in the sample, so this is the most likely cause rather than a confirmed one. Run this to confirm:

```sql
select payment_method,
       count(*) as payments,
       sum(amount) / 100.0 as dollars
from stg_payments p
where p.order_id is null
   or not exists (select 1 from stg_orders o where o.order_id = p.order_id)
group by 1;
```

If the coupon rows come back at 26.73 for March and 32.67 for April, that confirms it. If the `order_id`s are populated and refer to real order ids that simply aren't in `raw_orders`, the ingestion job dropped those orders and the fix is in the loader. If the `order_id`s are NULL, the ingestion job wrote the coupon rows without the order link.

Why the build stayed green: the tests cover uniqueness and not-null on each model's own columns, and the accepted-values checks passed. Nothing tests `stg_payments.order_id` against `stg_orders`, and nothing reconciles payment totals to order totals.

Side note, not related to the coupon gap: `models/region_revenue.sql` joins `raw_customer_regions`, which has an `updated_at` column. If a customer has more than one row there, `region_revenue` fans out and double-counts revenue. The seed's `not_null` tests won't catch this.

## 2. Fix

The model cannot invent the missing orders, so the fix has two parts.

**a. Repair the data at the source.** Reload the missing orders into `raw_orders`, or correct the `order_id` on the coupon payments in `raw_payments`, depending on which case the query above shows. Then rebuild:

```bash
dbt build --select stg_payments+ 
```

Rebuilding `orders` and everything downstream also refreshes `customers`. `daily_revenue` is incremental and only rebuilds the last three days, so run it with `--full-refresh` to correct earlier months:

```bash
dbt build --select daily_revenue+ --full-refresh
```

**b. Stop orders from dropping payments silently.** Add an explicit check in `models/staging/schema.yml`:

```yaml
  - name: stg_payments
    columns:
      - name: payment_id
        tests:
          - unique
          - not_null
      - name: order_id
        tests:
          - not_null
          - relationships:
              arguments:
                to: ref('stg_orders')
                field: order_id
      - name: payment_method
        tests:
          - accepted_values:
              arguments:
                values: ['credit_card', 'coupon', 'bank_transfer', 'gift_card']
```

With this in place the nightly build fails when payments are orphaned, which is the behavior you want. If you need the build to keep running while the source is fixed, set `severity: warn` on the `relationships` test for now.

## 3. A test that would have caught this

The check that matters here is a reconciliation test: total payments must equal total order amounts. Add `tests/assert_payments_reconcile_to_orders.sql`:

```sql
-- Returns a row (and so fails) if any payment does not reach orders.amount.
select
    date_trunc('month', o.order_date) as month,
    sum(o.amount) as order_amount,
    (select sum(amount) from {{ ref('stg_payments') }}) as payment_amount
from {{ ref('orders') }} o
group by 1
having round(sum(o.amount), 2) <> round((select sum(amount) from {{ ref('stg_payments') }}), 2)
```

This compares the grand total only, so it fails when any payment is orphaned, but it can't say which month. For a per-month breakdown, group the payments side by month too, using `payments.order_id` joined to `orders.order_date` or the payment's own timestamp if the source has one.

The `relationships` test on `stg_payments.order_id` is the more direct check: it names the offending `order_id`s, and it would have failed on the first night the March coupon payments were loaded without a matching order.
