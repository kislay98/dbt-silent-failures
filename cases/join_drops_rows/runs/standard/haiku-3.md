claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 57 s | USD 0.009 | 2026-10-14 | project ad0b16b | prompt 3fdc8e98d7e9

## 1. Root cause

**What's wrong:** `orders.sql` only sees payments that match a row in `stg_orders`. A payment with a NULL `order_id`, or an `order_id` with no matching order, is dropped without any error. `orders.amount`, `region_revenue.revenue`, and `customers.customer_lifetime_value` all inherit that gap, which is why all three sum to 1532.00. `stg_payments.sql` is correct: it keeps every payment and its 1672.00 total matches the processor.

**Where the data is missing:** `raw_payments` has payments with no order. Two appear in the samples:
- payment id 91, `order_id` NULL, 900 cents (9.00)
- payment id 15, `order_id` NULL, 14.00 (in `stg_payments`)

The gap is 1672.00 − 1532.00 = 140.00. The samples can't account for all of it, so some of the remaining 117.00 is probably more NULL or orphaned `order_id`s.

**Order 25:** The customer paid 58.00 and the order shows 42.00, so 16.00 is missing. My best guess is that one of order 25's three payments has a NULL `order_id` in the feed. That is consistent with the data but not confirmed, because I can't see the order 25 payment rows.

**Why the tests pass:** Nothing checks that payments are attributed to orders. `stg_payments.order_id` has no `not_null` or `relationships` test, and no test reconciles payment totals with order totals. `orders.amount` only has `not_null`, which holds because all 99 orders have at least one payment.

**Second symptom:** `daily_revenue` sums to 1672.00, the settlement figure, while `orders` sums to 1532.00. Both read the same `orders` table, so `daily_revenue` is stale. It is incremental and only rebuilds the last three days, so older rows still hold values from when those payments were attached. Anything older than three days won't correct itself.

To confirm, run this against the warehouse:

```sql
select p.*
from raw_payments p
where p.order_id is null
   or p.order_id not in (select id from raw_orders);
```

The amounts should sum to 140.00, and one of the rows should be the 16.00 payment for order 25.

## 2. Fix

**Data fix (the real one):** Restore `order_id` on the unattributed rows from the processor's source feed. Don't match them by amount or timing, because that would invent attributions.

**Code fix (so this can't fail silently again):**

- Keep `stg_payments` as it is. It is correct for settlement.
- Add a model that surfaces the gap, for example `models/unattributed_payments.sql`:

```sql
select *
from {{ ref('stg_payments') }}
where order_id is null
   or order_id not in (select order_id from {{ ref('stg_orders') }})
```

- Rebuild the stale incremental table after the data is fixed:

```
dbt build --select daily_revenue --full-refresh
```

Then run `dbt build`. Once the order 25 payment is attributed, `orders` should show 58.00 for that order and the totals should match 1672.00.

**Choices I made that you can redirect:** I kept the fix in data plus a visible model rather than having `orders.sql` guess at attribution or drop rows with a filter. I also did not change `region_revenue`, since it inherits the fix once the data is corrected.

## 3. Test that would have caught it

Add tests to `models/staging/schema.yml`, matching the existing `arguments:` style:

```yaml
  - name: stg_payments
    columns:
      - name: order_id
        tests:
          - not_null
          - relationships:
              arguments:
                to: ref('stg_orders')
                field: order_id
```

Add a reconciliation test in `tests/assert_orders_reconcile_to_payments.sql`. It returns a row, and so fails, whenever the totals differ:

```sql
with payments_total as (
    select round(sum(amount), 2) as total from {{ ref('stg_payments') }}
),

orders_total as (
    select round(sum(amount), 2) as total from {{ ref('orders') }}
)

select *
from payments_total
cross join orders_total
where payments_total.total <> orders_total.total
```

On the current data, both new tests should fail, which is the correct result. The `not_null` and `relationships` tests point at the payments with NULL or unknown `order_id`, and the reconciliation test reports the 140.00 gap.

**Other items I noticed, not related to this bug:**
- `region_revenue` has `unique` on `region`, but `region` is the group key, so that test can never fail. Add a `unique` test on `raw_customer_regions.customer_id` instead, so a customer with two region rows can't fan out revenue.
- `customers` documents `total_order_amount`, which doesn't exist in the model.
