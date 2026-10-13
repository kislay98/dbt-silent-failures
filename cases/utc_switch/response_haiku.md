## 1. Root cause

**Files:** `models/staging/stg_orders.sql` (the `cast(ordered_at as date)` line), fed by the `raw_orders` seed. The bad data is in `raw_orders.ordered_at`.

`ordered_at` is a naive `TIMESTAMP` with no zone. The admin screen and the app's day boundary use shop-local time, which is UTC+5:30 from the numbers you gave (19:45:20 stored, 01:15:20 next day on screen). The stored values are not in one zone. Rows from around mid-March appear to be UTC, while earlier rows are shop-local. `stg_orders` casts the raw value straight to a date, so UTC rows land on the previous local day.

The offset explains all four discrepancies. I shifted each stored `ordered_at` by +5:30 and recounted:

| Day | Stored (`orders`) | Shifted | App |
|---|---|---|---|
| 26 Mar | 5 (84–88) | 3 (84, 85, 86) | 3 |
| 27 Mar | 1 (89) | 2 (87, 88) | 2 |
| 28 Mar | 0 | 1 (89) | 1 |

Order 73 fits as well. Monthly totals match because a +5:30 shift only moves rows across a month boundary at month end, and `sum(amount)` is unchanged.

Two things I could not verify from what you sent:

- **Direction of the change.** The "always agreed" history implies the raw feed switched from local to UTC in mid-March. It is also possible the admin screen changed its display zone. Check the admin timezone setting history to rule that out.
- **Which rows are shifted.** Orders 78–82 on 23 March are also near the 18:30 UTC boundary, and you did not mention 23 or 24 March as mismatched. The shift may apply per row rather than from a single cutover date. Get the admin daily counts for the whole period to find the exact rows.

**Also wrong, in the docs:** `schema.yml` describes `order_date` as "Date (UTC)". The business definition is shop-local, so the description contradicts what ops reconciles against.

**Why tests pass:** `unique`, `not_null`, and `relationships` are all still true after a timestamp shifts. Nothing checks what the date means.

## 2. Fix

**Data (the real fix):** restate the post-cutover `raw_orders.ordered_at` values to shop-local time at the source or in the seed CSV, and confirm the shop's zone. The `AUD` in the docs does not match +5:30 in March 2018 (Australian zones were +10 or +11), so get the zone from the business rather than assuming it.

**Model (makes the contract explicit):** add a zone column to the raw data and convert in staging, so nothing depends on which rows happen to be which zone:

```sql
-- stg_orders.sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,

        -- raw ordered_at is naive; ordered_at_tz says which zone it was written in
        case
            when ordered_at_tz = 'UTC'
                then ordered_at at time zone 'UTC' at time zone '{{ var("shop_timezone") }}'
            else ordered_at
        end as ordered_at,

        cast(
            case
                when ordered_at_tz = 'UTC'
                    then ordered_at at time zone 'UTC' at time zone '{{ var("shop_timezone") }}'
                else ordered_at
            end as date
        ) as order_date,

        status

    from source

)
```

Set `shop_timezone` in `dbt_project.yml` under `vars:`. Update the `orders.order_date` and `stg_orders` descriptions to say "shop-local date". Use the column names your data actually has. I'm assuming `ordered_at_tz`, which does not exist yet.

**Downstream:** `daily_revenue` is incremental and only rebuilds the last three days. After the fix, run:

```
dbt run --select daily_revenue --full-refresh
```

Otherwise the historical days stay wrong.

## 3. Test that would have caught it

**Unit test** for the conversion logic, with order 73 as the fixture (dbt 1.8+):

```yaml
# models/staging/schema.yml
unit_tests:
  - name: stg_orders_order_date_is_shop_local
    model: stg_orders
    given:
      - input: ref('raw_orders')
        rows:
          - {id: 73, user_id: 1, ordered_at: '2018-03-15 19:45:20', ordered_at_tz: 'UTC', status: 'completed'}
          - {id: 74, user_id: 1, ordered_at: '2018-03-15 10:00:00', ordered_at_tz: 'local', status: 'completed'}
    expect:
      rows:
        - {order_id: 73, order_date: '2018-03-16'}
        - {order_id: 74, order_date: '2018-03-15'}
```

**Reconciliation test** against the admin export, which catches the data problem itself. Load the admin's daily counts as a seed (`admin_daily_orders`, columns `order_date`, `admin_orders`) and add a singular test:

```sql
-- tests/assert_daily_orders_match_admin.sql
-- returns rows only when a day disagrees, so the test fails
select
    coalesce(o.order_date, a.order_date) as order_date,
    coalesce(o.orders, 0) as model_orders,
    coalesce(a.admin_orders, 0) as admin_orders
from (
    select order_date, count(*) as orders
    from {{ ref('orders') }}
    group by 1
) o
full outer join {{ ref('admin_daily_orders') }} a
    on o.order_date = a.order_date
where coalesce(o.orders, 0) <> coalesce(a.admin_orders, 0)
```

The unit test guards the logic. The reconciliation test guards the data, and it would have failed on 26 March. Ops' manual morning check becomes an automated failure.
