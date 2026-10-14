claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 29 s | USD 0.102 | 2026-10-15 | project ad0b16b | prompt 63b47184243a

## 1. Root cause

**Caveat:** the `raw_orders` seed rows weren't included, so the evidence below is inferred from the schema, the sampled rows and order 73, and the cutoff date still needs confirming.

The problem is in `models/staging/stg_orders.sql`, and in the data it reads (`seeds/raw_orders.csv`):

```sql
ordered_at,
cast(ordered_at as date) as order_date,
```

`raw_orders.ordered_at` is a zone-less `TIMESTAMP`. Nothing in the project says what time zone it is in, and `models/schema.yml` documents `order_date` as "Date (UTC)". The code simply truncates whatever clock the feed uses.

- Order 73 is `2018-03-15 19:45:20` here and `2018-03-16 01:15:20` in the app, a gap of exactly **+5h30m**. The app shows shop-local time.
- Until mid-March the two agreed, so the older rows must have been loaded in shop-local time. Rows from about 15 March onward appear to be UTC, so the feed changed its clock without any schema change.
- Any UTC row after about 18:30 belongs to the next local day. That moves orders from the 26th to the 27th and from the 27th to the 28th, which matches 26 March (5 vs 3), 27 March (1 vs 2) and 28 March (0 vs 1). For example, order 88 at `2018-03-26 21:51:20` is 27 March 03:21 locally.
- Monthly totals match because the shifts only cross day boundaries, not month boundaries.
- `sum(amount)` is unaffected because no payments are lost, only moved to a different date.
- All tests pass because none of them look at the meaning of the timestamp. `unique`, `not_null` and `accepted_values` can't detect a clock change.

## 2. Fix

Check the first fix against the app: find the earliest row where the UTC-to-local shift is needed, which is somewhere between the last agreeing day and order 73. The best long-term fix is to make the feed consistent upstream, but a staging fix also works:

```sql
-- models/staging/stg_orders.sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,
        -- feed switched from shop-local to UTC at {{ var('orders_utc_from') }}; normalise to shop-local
        case
            when ordered_at >= timestamp '{{ var("orders_utc_from") }}'
                then ordered_at + interval '{{ var("shop_utc_offset_minutes") }} minutes'
            else ordered_at
        end as ordered_at,
        status

    from source

),

with_date as (

    select *, cast(ordered_at as date) as order_date from renamed

)

select * from with_date
```

```yaml
# dbt_project.yml
vars:
  orders_utc_from: '2018-03-15 00:00:00'   # confirm exact cutoff against the app
  shop_utc_offset_minutes: 330             # +05:30 inferred from order 73; confirm with ops
```

- A fixed offset is only correct if the shop's zone has no DST. If it does, use `timezone('<zone>', ordered_at at time zone 'UTC')` instead.
- Update the `order_date` descriptions in `schema.yml` and `customers.first_order` and `most_recent_order` from "UTC" to "shop-local".
- `customers.first_order` and `most_recent_order` also shift, so they are fixed by the same change.
- **Run `dbt build --full-refresh -s daily_revenue+`.** `daily_revenue` only rebuilds the last 3 days, so the wrongly dated March rows would otherwise stay as they are.

## 3. Tests

**Reconciliation test (this would have caught it).** Load the app's daily counts as a seed and compare:

```sql
-- tests/assert_daily_orders_match_app.sql
select coalesce(d.order_date, a.order_date) as order_date, d.orders, a.orders as app_orders
from {{ ref('daily_revenue') }} d
full outer join {{ ref('app_daily_order_counts') }} a using (order_date)
where coalesce(d.orders, 0) != coalesce(a.orders, 0)
```

**Unit test (pins the behaviour for order 73):**

```yaml
unit_tests:
  - name: stg_orders_converts_utc_to_shop_local
    model: stg_orders
    given:
      - input: ref('raw_orders')
        rows:
          - {id: 73, user_id: 19, ordered_at: '2018-03-15 19:45:20', status: completed}
    expect:
      rows:
        - {order_id: 73, customer_id: 19, ordered_at: '2018-03-16 01:15:20', order_date: '2018-03-16', status: completed}
```
