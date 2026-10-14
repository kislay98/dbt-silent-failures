claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 36 s | USD 0.007 | 2026-10-15 | project ad0b16b | prompt ef736a9770e3

## 1. Root cause

**`jaffle_shop/models/staging/stg_orders.sql`** derives the order date from a UTC timestamp:

```sql
ordered_at,
cast(ordered_at as date) as order_date,
```

`raw_orders.ordered_at` is a naive `TIMESTAMP` that is stored in UTC. The `cast(... as date)` takes the UTC calendar day, but the admin screen shows the local day in India (Asia/Kolkata, UTC+05:30).

Order 73 confirms this. Its raw value is `2018-03-15 19:45:20`. Adding 5h30m gives `2018-03-16 01:15:20`, which is the time the admin screen shows. The warehouse puts it on 15 March because it uses the UTC date.

This explains the pattern:

- Only orders placed between 18:30 and 23:59:59 UTC move to the next local day. Those are the orders that flip between the two views.
- Monthly totals agree because the shift stays inside the month except on the last day of a month.
- The January and March windows show disagreements because orders in those windows fall in that UTC evening range.

I can see the mechanism from the data, but I can't compute the exact per-day differences from what was provided. The `orders` table is downstream of this bug, so it inherits the wrong dates. `customers.first_order` and `most_recent_order` do too.

`daily_revenue` is also wrong by the code as written. It groups `orders.order_date`, which is the UTC date. The claim that it matches the admin screen for January to March doesn't fit that code. The likely explanation is that the old rows were written before this logic existed. The incremental filter only rebuilds the last three days, so those old rows never get corrected. I'd check this directly with the query in section 2.

The schema descriptions are also wrong. `schema.yml` says `order_date` is "Date (UTC)", and that documentation is what made the UTC choice look deliberate.

## 2. Fix

Convert to the local day in staging, which is the single place where `order_date` is defined:

```sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,
        ordered_at,
        cast((ordered_at at time zone 'UTC') at time zone 'Asia/Kolkata' as date) as order_date,
        status

    from source

)
```

Update the descriptions in `jaffle_shop/models/schema.yml` for `orders.order_date`, `customers.first_order`, and `customers.most_recent_order`, changing "Date (UTC)" to "Date (IST, Asia/Kolkata)".

Because `daily_revenue` is incremental, a normal run will not correct its historical rows. After the change, rebuild the downstream models with a full refresh:

```bash
dbt build --select stg_orders+ --full-refresh
```

Before and after the rebuild, confirm the result against the source:

```sql
select cast((ordered_at at time zone 'UTC') at time zone 'Asia/Kolkata' as date) as local_day,
       count(*) as orders
from raw_orders
group by 1
order by 1;
```

Compare this with `daily_revenue` and with the admin screen. Expect them to match for every day. If `daily_revenue` already matched the admin screen before the fix, this check will show where its stale rows came from.

## 3. Test that would have caught this

The existing tests check uniqueness, not-null, accepted values, and relationships. None of them check what the date means. A dbt unit test would have caught it. It pins the conversion, including the 18:30 UTC boundary. Add this to `jaffle_shop/models/staging/schema.yml`:

```yaml
unit_tests:
  - name: stg_orders_order_date_is_local_day
    description: order_date is the Asia/Kolkata calendar day of ordered_at (UTC)
    model: stg_orders
    given:
      - input: ref('raw_orders')
        rows:
          - {id: 1, user_id: 10, ordered_at: '2018-03-15 10:00:00', status: completed}
          - {id: 2, user_id: 10, ordered_at: '2018-03-15 18:29:59', status: completed}
          - {id: 3, user_id: 10, ordered_at: '2018-03-15 18:30:00', status: completed}
          - {id: 73, user_id: 19, ordered_at: '2018-03-15 19:45:20', status: completed}
    expect:
      rows:
        - {order_id: 1, order_date: '2018-03-15'}
        - {order_id: 2, order_date: '2018-03-15'}
        - {order_id: 3, order_date: '2018-03-16'}
        - {order_id: 73, order_date: '2018-03-16'}
```

Run it with `dbt test --select stg_orders`. It fails on the current code and passes after the fix.

I haven't run any of this. I can't run the project from here, so the unit test and the verification query are untested.
