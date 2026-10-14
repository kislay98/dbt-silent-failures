claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 34 s | USD 0.082 | 2026-10-14 | project 7d5b525+dirty

## 1. Root cause

**`models/staging/stg_orders.sql`** (`cast(ordered_at as date) as order_date`) combined with a change in the data in **`seeds/raw_orders.csv`** (`raw_orders.ordered_at`, a naive `TIMESTAMP`).

- The admin screen uses shop-local time, which is IST (UTC+5:30). Order 73 shows 2018-03-16 01:15:20 there and 2018-03-15 19:45:20 here, which is exactly 5h30m earlier.
- The model casts `ordered_at` to a date as-is. That was correct while the feed held shop-local timestamps, and ops reconciled cleanly.
- From about order 73 (mid-March) the feed's `ordered_at` is **UTC**, not local. The column type can't show that, because it is a naive timestamp in both cases. Orders placed after 18:30 UTC now land on the previous local day.
- Checking the sample against a +5:30 shift reproduces ops' numbers exactly:
  - 26 March: orders 84, 85 and 86 stay on the 26th, so 3 orders. Orders 87 (21:12 UTC) and 88 (21:51 UTC) move to the 27th.
  - 27 March: orders 87 and 88, so 2 orders. Order 89 (18:45 UTC) moves to the 28th.
  - 28 March: order 89, so 1 order.
- Monthly totals and `sum(amount)` still match because only the day boundary moves. Month boundaries are only affected if an order falls in the shifted window, and none did here.
- Every test passed because none of them check values. They check uniqueness, nulls and accepted values, and a shifted date satisfies all of those. The `orders.order_date` docs also say "Date (UTC)", which hides the mismatch.
- `customers.first_order` and `most_recent_order`, and `daily_revenue`, are affected too.

I couldn't see the seed, so the cutover point is inferred. Confirm it by comparing orders 68–73 against the admin screen. I'm assuming ids are chronological and that 73 is the first UTC row.

## 2. Fix

Normalise to shop-local time in staging so everything downstream stays correct. Better still, ask the feed owner to restore local time or add a timezone column, then delete this shim.

```sql
-- stg_orders.sql
{% set utc_from_order_id = var('orders_utc_from_order_id', 73) %}
{% set shop_utc_offset_minutes = 330 %}  -- IST

renamed as (

    select
        id as order_id,
        user_id as customer_id,
        -- feed switched from shop-local to UTC at this order; normalise to shop-local
        case
            when id >= {{ utc_from_order_id }}
                then ordered_at + interval {{ shop_utc_offset_minutes }} minute
            else ordered_at
        end as ordered_at,
        cast(
            case
                when id >= {{ utc_from_order_id }}
                    then ordered_at + interval {{ shop_utc_offset_minutes }} minute
                else ordered_at
            end as date
        ) as order_date,
        status

    from source

)
```

Also change the `order_date`, `first_order` and `most_recent_order` descriptions in `schema.yml` from "Date (UTC)" to "Date (shop local time, IST)".

**`daily_revenue` needs a full refresh.** It only rebuilds the last 3 days, so the wrongly bucketed March rows would otherwise stay:

```
dbt build --full-refresh -s stg_orders+ 
```

## 3. Tests that would have caught it

**a) Unit test pinning the timezone behaviour** (in `models/staging/schema.yml`):

```yaml
unit_tests:
  - name: stg_orders_order_date_is_shop_local
    model: stg_orders
    given:
      - input: ref('raw_orders')
        rows:
          - {id: 10, user_id: 1, ordered_at: '2018-02-01 19:45:20', status: placed}
          - {id: 73, user_id: 1, ordered_at: '2018-03-15 19:45:20', status: placed}
    expect:
      rows:
        - {order_id: 10, customer_id: 1, ordered_at: '2018-02-01 19:45:20', order_date: '2018-02-01', status: placed}
        - {order_id: 73, customer_id: 1, ordered_at: '2018-03-16 01:15:20', order_date: '2018-03-16', status: placed}
```

**b) Reconciliation test against the numbers ops already check.** Load the admin screen's daily counts as a seed (`seeds/admin_daily_orders.csv` with `order_date,orders`) and add `tests/assert_daily_orders_match_admin.sql`:

```sql
select
    coalesce(o.order_date, a.order_date) as order_date,
    o.orders as dbt_orders,
    a.orders as admin_orders
from (select order_date, count(*) as orders from {{ ref('orders') }} group by 1) o
full outer join {{ ref('admin_daily_orders') }} a using (order_date)
where coalesce(o.orders, 0) != coalesce(a.orders, 0)
```

Set it to `severity: warn` if the seed lags the latest days. Test (b) fails on exactly 26, 27 and 28 March before the fix.
