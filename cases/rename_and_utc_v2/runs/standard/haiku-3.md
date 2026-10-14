claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 52 s | USD 0.009 | 2026-10-15 | project ad0b16b | prompt 15c0aae6d239

## 1. Root cause

**The 43 missing dates come from `stg_orders.sql` reading only `ordered_at`.** `jaffle_shop/models/staging/stg_orders.sql` builds `order_date` as `cast(ordered_at as date)`. The raw load now leaves `ordered_at` NULL for some rows and writes the timestamp to `order_ts`, which the model never reads. So `order_date` is NULL for those rows.

- The 43 NULL-date orders are exactly the March and April orders: `daily_revenue` shows 35 + 8 = 43 orders for those months, and 622.00 + 139.00 = 761.00, which matches the NULL bucket's amount in the orders report.
- `orders.sql` passes `order_date` through unchanged, so `orders` carries the NULLs. The monthly report then shows them in a NULL bucket.
- `customers.sql` is affected too. `min(order_date)` and `max(order_date)` skip NULLs, so customers whose only recent orders are NULL-dated (for example customer 9 and customer 47 in the sample) show `first_order` NULL while `number_of_orders` is non-zero.
- `daily_revenue.sql` is incremental. Its `where order_date >= ...` filter and `group by` drop the NULL rows, and `delete+insert` only replaces keys present in the new batch. The March and April rows from the earlier build are never touched, which is why they still look right. They are stale, not correct. Any full refresh would remove them.

**Why the tests pass:** nothing asserts `orders.order_date` is not null. The `daily_revenue` tests only check that model's own output, which has no NULLs. `sum(amount)` is 1672.00 either way, so no aggregate test notices the lost dates.

**A second, related problem: timezone.** The `order_date` description says "Date (UTC)", but ops reconciles against the admin screen in shop-local time. For order 73, `order_ts` 2018-03-15 19:45:20 corresponds to 2018-03-16 01:15:20 on the admin screen, an offset of +05:30. A plain `cast(order_ts as date)` would give 03-15 and still disagree with ops. I can't tell from the material which zone the shop uses; +05:30 doesn't match an AUD market for that date, so confirm the zone from the admin system's settings.

There's also a conflict in the data. On the rows where both columns are populated, `ordered_at` and `order_ts` are identical, and ops says the pre-March dates agreed with the admin screen. That suggests `ordered_at` was already on the shop's clock, while the new `order_ts` is UTC. The sample alone can't settle this, so check it before applying the timezone conversion below.

## 2. Fix

**Immediate, in `jaffle_shop/models/staging/stg_orders.sql`:** use `ordered_at` when present and fall back to `order_ts` converted to shop time when it is NULL. Replace `<shop_tz>` with the zone verified above.

```sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,
        coalesce(
            ordered_at,
            (order_ts at time zone 'UTC') at time zone '<shop_tz>'
        ) as ordered_at,
        cast(
            coalesce(
                ordered_at,
                (order_ts at time zone 'UTC') at time zone '<shop_tz>'
            ) as date
        ) as order_date,
        status

    from source

)
```

The conversion only applies to rows where `ordered_at` is NULL. If the verification above shows `order_ts` is already local, drop the `at time zone` part and keep the plain `coalesce`.

Then rebuild `daily_revenue` from scratch. The incremental window won't reach March and April, and rows that move days would otherwise be double-counted:

```
dbt build --select stg_orders+ --full-refresh
```

**Real fix (upstream):** the ingestion job should populate `ordered_at` again, or the source should be renamed consistently and the model updated to read it. The fallback above is a stopgap.

## 3. Test that would have caught it

Add `not_null` to `orders.order_date` in `jaffle_shop/models/schema.yml`. The build would have failed on 43 rows:

```yaml
      - name: order_date
        description: Date (in shop local time) that the order was placed
        tests:
          - not_null
```

Also add `not_null` on `customers.first_order` where `number_of_orders > 0`, as a singular test. It catches the customer-side symptom:

```sql
-- tests/assert_customers_with_orders_have_first_order.sql
select customer_id
from {{ ref('customers') }}
where number_of_orders > 0
  and first_order is null
```

These tests are the minimum. A stronger check would reconcile `count(*)` and `sum(amount)` from `raw_orders` against `orders` with the same date basis as the admin screen, so that a dropped date fails the build as well.

I worked only from the pasted files and output. I haven't run anything, so the timezone and the `ordered_at`/`order_ts` relationship need checking against the live warehouse before the fix is applied.
