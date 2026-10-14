claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 33 s | USD 0.104 | 2026-10-15 | project ad0b16b | prompt 11fb6c29d933

## 1. Root cause

The nightly load of `raw_orders` changed shape. From March on, `ordered_at` is NULL and the timestamp arrives only in the new `order_ts` column. The sample rows show this: order 63 has `ordered_at` NULL and `order_ts` 2018-03-06 12:28:52. Jan/Feb rows have both columns populated.

`models/staging/stg_orders.sql` still reads only `ordered_at`: `cast(ordered_at as date) as order_date`. So the 43 March/April orders get `order_date` NULL and fall out of `date_trunc('month', ...)` into the NULL bucket (43 orders, 761.00 amount). The Jan/Feb buckets are intact, and the total of 1672.00 still matches because the NULL-date rows are still summed.

Why the tests stayed green:
- No `not_null` test exists on `stg_orders.order_date` or `orders.order_date`.
- `daily_revenue` is incremental, so its March/April rows are stale leftovers from the previous load. Its filter `order_date >= max(order_date) - 3 days` never matches NULL dates, so the 43 orders are silently excluded rather than erroring.
- `unique` and `not_null` on `daily_revenue.order_date` can't see rows that were never inserted.

**Timezone trap.** The admin screen shows shop-local time, and Jan–Feb `ordered_at` agreed with it. `cast(order_ts as date)` is only safe if `order_ts` is also local. If `order_ts` is UTC, orders placed between local midnight and 11:00 AEDT land on the previous day. Order 73 (01:15:20 local on 2018-03-16) is a case of that. I don't have `order_ts` for order 73, so I couldn't confirm this. Check it first:

```sql
select id, ordered_at, order_ts from raw_orders where id = 73;
```
- If `order_ts` = `2018-03-15 14:15:20`, it is UTC and the Sydney/Melbourne offset is +11 (AEDT). Convert it.
- If it is `2018-03-16 01:15:20`, it is already local and no conversion is needed.

## 2. Fix

`stg_orders.sql`, assuming UTC. Use a named zone, not a fixed offset, because AEDT ends on 2018-04-01 and April orders are +10. I used `Australia/Sydney`; confirm the shop's actual zone, since Brisbane and Perth don't observe the same DST.

```sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,
        -- ordered_at (shop-local) was retired in March; order_ts is its replacement
        coalesce(
            ordered_at,
            timezone('Australia/Sydney', timezone('UTC', order_ts))
        ) as ordered_at,
        cast(coalesce(
            ordered_at,
            timezone('Australia/Sydney', timezone('UTC', order_ts))
        ) as date) as order_date,
        status

    from source

)
```

`coalesce` keeps the Jan/Feb rows untouched, where `ordered_at` equals `order_ts` and is already local. If order 73 shows `order_ts` is already local, use `coalesce(ordered_at, order_ts)` instead.

Then rebuild:
- Run `dbt run --full-refresh -s daily_revenue+ stg_orders+`. A normal run won't repair `daily_revenue`, because its window only reaches back 3 days and the March dates may now shift.
- Update the `order_date` descriptions in `schema.yml` (`orders` and `customers`). They say "Date (UTC)", but the data is shop-local.
- Ask ingestion whether `ordered_at` will return, or drop it from the contract.

## 3. Tests

Add the missing not-null checks to `models/staging/schema.yml`, and the same one to `orders.order_date` in `models/schema.yml`:

```yaml
      - name: order_date
        tests:
          - not_null
```

Add a unit test (dbt 1.8+) that pins the timezone behaviour, in `models/staging/schema.yml`:

```yaml
unit_tests:
  - name: stg_orders_order_date_from_order_ts
    model: stg_orders
    given:
      - input: ref('raw_orders')
        rows:
          - {id: 73, user_id: 1, ordered_at: null, order_ts: '2018-03-15 14:15:20', status: placed}
    expect:
      rows:
        - {order_id: 73, order_date: '2018-03-16'}
```

Add a singular test, `tests/daily_revenue_reconciles_orders.sql`, that fails when the incremental table drifts from `orders`:

```sql
select 'orders' as src, sum(amount) from {{ ref('orders') }}
having sum(amount) <> (select sum(revenue) from {{ ref('daily_revenue') }})
```

The not-null test on `order_date` alone would have failed on this load. The reconciliation test catches the same silent loss in `daily_revenue` for any future cause.
