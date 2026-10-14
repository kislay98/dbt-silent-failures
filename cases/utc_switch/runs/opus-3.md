claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 63 s | USD 0.199 | 2026-10-14 | project f769fdf | prompt fabc2138986f

The project and seed CSVs aren't on this machine, so this diagnosis is based only on what you pasted. That's enough to pin down the cause, but one detail needs checking against the seed (see "Caveat").

## 1. Root cause

The bad data is in **`seeds/raw_orders.csv`**: starting partway through March, the timezone of `ordered_at` changed.

- `raw_orders.ordered_at` is a plain `TIMESTAMP` with no timezone. There's no offset in the data, so nothing records which zone a value is in.
- Order 73 shows as `2018-03-16 01:15:20` local but `2018-03-15 19:45:20` here. That's exactly **5 h 30 m behind** local time. The rows from mid-March onward look like they were exported in UTC with a +05:30 shop zone. Earlier rows were in shop-local time, which is why the reconciliation always matched before.
- **`models/staging/stg_orders.sql`** runs `cast(ordered_at as date)`, which takes the date part as written. Any order placed between 00:00 and 05:30 local time therefore lands on the **previous** day. Your mismatches fit this exactly:

| order | `ordered_at` here | +5:30 = local | counted here | app |
|---|---|---|---|---|
| 87 | 03-26 21:12 | 03-27 02:42 | 26th | 27th |
| 88 | 03-26 21:51 | 03-27 03:21 | 26th | 27th |
| 89 | 03-27 18:45 | 03-28 00:15 | 27th | 28th |

  → 26: 5 vs 3, 27: 1 vs 2, 28: 0 vs 1. ✔

- Monthly totals and `sum(amount)` still match because each order moves by only a few hours. The same orders and payments are present, so only the day buckets change. Every existing test checks keys, nulls or enum values. None of them looks at timestamps, so the build passes.
- Related problem: `models/schema.yml` describes `order_date` as "Date (UTC)". That was never what ops used it for, and the new rows have made the description and the data disagree.

**Caveat:** if *every* row from the switch onward is shifted by 5:30, then order 82 (`03-23 23:44:48` → local `03-24 05:14`) should also move. That would make 23 and 24 March off by one (4 vs 5 and 2 vs 1 in the app). Please check whether ops sees that. If they don't, the switch didn't happen cleanly at a single order. In that case, compare the seed with the admin screen to find exactly which rows are affected before applying the fix below.

## 2. Fix

The best fix is at the source: have the export send timestamps with an explicit offset, or consistently in UTC, and re-export `raw_orders.csv`. Until that happens, normalize the data in staging and derive the date explicitly in shop time:

```sql
-- models/staging/stg_orders.sql
with source as (

    {#-
    Normally we would select from the table here, but we are using seeds to load
    our data in this project
    #}
    select * from {{ ref('raw_orders') }}

),

normalized as (

    select
        *,
        -- the export switched from shop-local to UTC part-way through March 2018
        case
            when id >= {{ var('orders_utc_from_id') }}
                then ordered_at
            else (ordered_at at time zone '{{ var("shop_timezone") }}') at time zone 'UTC'
        end as ordered_at_utc

    from source

),

renamed as (

    select
        id as order_id,
        user_id as customer_id,
        ordered_at_utc as ordered_at,
        cast((ordered_at_utc at time zone 'UTC') at time zone '{{ var("shop_timezone") }}' as date) as order_date,
        status

    from normalized

)

select * from renamed
```

```yaml
# dbt_project.yml
vars:
  shop_timezone: 'Asia/Kolkata'   # +05:30 per order 73 — confirm with ops
  orders_utc_from_id: 73          # first order exported in UTC — confirm against the admin screen
```

After this change, `ordered_at` is always UTC and `order_date` is the shop-local date. Update the `order_date` description in `models/schema.yml` (and `first_order`/`most_recent_order`) to say "shop-local date" instead of "UTC".

**Important:** `daily_revenue` is incremental and only rebuilds the last 3 days, so the wrong March rows already in it won't be corrected on their own. Run:

```
dbt build --full-refresh -s daily_revenue+
```

## 3. Tests that would have caught it

**a) Unit test** (dbt ≥ 1.8) that pins down how dates are derived. It uses order 73 as a known case:

```yaml
# models/staging/schema.yml
unit_tests:
  - name: order_date_is_shop_local
    model: stg_orders
    given:
      - input: ref('raw_orders')
        rows:
          - {id: 73, user_id: 1, ordered_at: '2018-03-15 19:45:20', status: placed}
    expect:
      rows:
        - {order_id: 73, order_date: '2018-03-16'}
```

**b) Reconciliation data test.** This is the one that would have caught the change in the data itself. Load the admin screen's daily counts as a seed (`app_daily_order_counts`: `order_date, orders`) and fail on any day that differs:

```sql
-- tests/assert_daily_orders_match_app.sql
select coalesce(o.order_date, a.order_date) as order_date, o.orders, a.orders as app_orders
from (select order_date, count(*) as orders from {{ ref('orders') }} group by 1) o
full outer join {{ ref('app_daily_order_counts') }} a using (order_date)
where o.orders is distinct from a.orders
```

Any totals-based test would pass whether or not the timezone is right. Only a check at the daily grain, compared against the system ops trusts, detects a shift like this, which is why (b) is the test that matters.
