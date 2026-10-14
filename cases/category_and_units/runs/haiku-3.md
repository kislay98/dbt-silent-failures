claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 95 s | USD 0.012 | 2026-10-14 | project 8ee5734 | prompt d0c0d63b3bb2

## Verdict

I can't name the exact rows or file responsible from what's here, because the seed and raw data aren't included. The arithmetic does narrow it down, though.

- The gap is exactly 136.00 in both windows: 279 − 143 = 136 for March, and 1103 − 967 = 136 since January.
- January and February completed revenue match between `orders` and the admin. Completed revenue in `orders` is 424 + 400 + 143 = 967, which equals `customers.completed_lifetime_value`.
- So the 136 is completed-order revenue dated in March that exists in the admin but not in the warehouse models.

I checked the transformations and none of them drop or inflate revenue for these rows:

- **`stg_orders` / `stg_payments`**: pass-through renames and `amount / 100`. No filtering.
- **`orders.sql`**: `stg_orders` is the left side, so no orders are dropped. Fan-out through `raw_payment_methods` would inflate `amount`, but `orders` and `customers` agree, so there is no duplicate `payment_method` row right now.
- **`customers.sql`**: the completed sums agree with `orders`, so no customer-level rows are lost.
- **Timezone**: a UTC/AUD date shift can't explain it, because the gap is the same for "since January" as for March.
- **`daily_revenue` / `region_revenue`**: neither is a status-filtered completed figure, so they aren't the source of this number.

That leaves two places the 136 can come from:

1. **Completed March payments or orders missing from the raw extract** (the ingestion job's load). The models rebuild from whatever is in `raw_orders` and `raw_payments` at build time. The `daily_revenue` comment says payments can land late, which fits.
2. **Payments whose `order_id` has no row in `raw_orders`** (orphans). The models silently drop them, and the admin, which has its own order status, counts them.

Confirm which with these queries against the warehouse:

```sql
-- 1. Payments with no order in the warehouse (hypothesis 2)
select p.order_id, p.amount
from stg_payments p
left join stg_orders o on p.order_id = o.order_id
where o.order_id is null;

-- 2. Anti-join: admin's March completed order IDs vs warehouse orders (either hypothesis)
-- Export the admin's list of March completed order IDs, load it as admin_march_completed, then:
select a.order_id
from admin_march_completed a
left join orders o on a.order_id = o.order_id
where o.order_id is null;
```

## Other defects in the files (not the cause of the 136, but real)

- **`tests` never run for the seeds.** The log says `Found 32 data tests` but `Finished running 28 data tests`. The four seed column tests (`raw_customer_regions.customer_id`, `region`, `raw_payment_methods.payment_method`, `fee_rate`) are the ones missing. The seeds are not in the nightly run.
- **`raw_customer_regions` has an `updated_at` column**, and its description says "one row per customer", but nothing enforces that. If a customer has two rows, `region_revenue` joins fan out and inflate `revenue`. Fix: keep the latest row per `customer_id`, e.g. `qualify row_number() over (partition by customer_id order by updated_at desc) = 1`, and add `unique` on `customer_id`.
- **`raw_payment_methods` has a `processor` column**, so a method could have several rows. Same fan-out risk in `orders.sql`, and no uniqueness test on `payment_method`.
- **No referential test from payments to orders.** `stg_payments.order_id` is never checked against `stg_orders`. That is why orphan payments pass silently.

## Fix

The root cause is upstream of the models, so the fix is in the ingestion job:

1. Reload the March `raw_payments` and `raw_orders` extracts (or fix the cut-off that is dropping them), then rebuild with `dbt build`.
2. Make the orphan case visible as a build failure rather than a silent drop. Add the tests below.
3. Apply the `raw_customer_regions` dedupe and the seed test wiring above.

## Test that would have caught this

Add to `jaffle_shop/models/staging/schema.yml`:

```yaml
  - name: stg_payments
    columns:
      - name: order_id
        tests:
          - relationships:
              arguments:
                to: ref('stg_orders')
                field: order_id
```

Add a singular test at `jaffle_shop/tests/assert_payments_reconcile_to_orders.sql`:

```sql
-- Every payment must roll up to an order. Returns rows (and fails) if payments exist without a matching order.
select round(sum(p.amount), 2) as payments_total, round(sum(o.amount), 2) as orders_total
from {{ ref('stg_payments') }} p
full outer join {{ ref('orders') }} o on p.order_id = o.order_id
having round(sum(p.amount), 2) <> round(sum(o.amount), 2)
```

The `relationships` test would have failed the nightly build as soon as an orphan appeared. The reconciliation test catches the same problem in dollar terms, which is what finance cares about.
