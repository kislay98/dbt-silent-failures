claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 57 s | USD 0.242 | 2026-10-15 | project ad0b16b | prompt 63b47184243a

I don't have the seed CSV (there's no `raw_orders.csv` in the working directory), so the diagnosis below comes from the evidence you pasted. Step 1 says how to confirm it.

## 1. Root cause

**The `ordered_at` values in `seeds/raw_orders.csv` stopped being written in shop-local time around mid-March. DuckDB silently shifts those rows when it loads the seed.** `models/staging/stg_orders.sql` then truncates the shifted values with `cast(ordered_at as date)`.

The evidence:

- **Order 73.** The admin screen shows 2018-03-16 01:15:20. The warehouse `raw_orders.ordered_at`, a plain `TIMESTAMP`, holds 2018-03-15 19:45:20. The shift happens before any model runs, in the raw table itself, and it is exactly 5h30m.
- **Daily counts.** The shift is always backwards, so orders placed early in the local morning fall into the previous date. 26 Mar gains 2 orders (+2), 27 Mar loses one but gains one from the 28th (−1), and 28 Mar loses its only order (−1). Every order stays in the same month, which is why monthly totals and `sum(amount)` = 1672.00 still match.
- **Why it only started mid-March.** Older rows still reconcile, so they are naive local wall-clock times. The newer rows must be in a different form. The most likely cause is that the export started writing a UTC offset, e.g. `2018-03-16 01:15:20+05:30`. DuckDB's CSV sniffer types the whole column as `TIMESTAMP`, and casting a string with an offset to `TIMESTAMP` normalises it to UTC and drops the offset: `'2018-03-16 01:15:20+05:30'::timestamp` gives `2018-03-15 19:45:20`. The other possibility is that the export switched to writing naive UTC.

To confirm, run `grep -nE '(\+|-)[0-9]{2}:?[0-9]{2}$|Z$' seeds/raw_orders.csv` on the seed and look at where the format changes.

**Why no test caught it:** every row still has a valid, non-null timestamp and every key is still unique. Nothing checks the format or timezone of `ordered_at`. Separately, `models/schema.yml` documents `order_date` as "Date (UTC)", but ops reconciles it as a local business date. The model was only correct because the source happened to be local time.

## 2. Fix

**Load the column as text so the raw value survives:**

```yaml
# dbt_project.yml
seeds:
  jaffle_shop:
    raw_orders:
      +column_types:
        ordered_at: varchar
```

**Normalise everything to shop-local time in staging (`models/staging/stg_orders.sql`):**

```sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,
        ordered_at as ordered_at_raw,
        case
            -- rows exported with an offset are converted to the shop's local time
            when regexp_matches(ordered_at, '(Z|[+-]\d{2}:?\d{2})$')
                then timezone('{{ var("shop_timezone") }}', cast(ordered_at as timestamptz))
            -- older rows are naive local wall-clock time
            else cast(ordered_at as timestamp)
        end as ordered_at,
        status

    from source

),

final as (

    select
        *,
        cast(ordered_at as date) as order_date
    from renamed

)

select * from final
```

Set `vars: {shop_timezone: '...'}` to the zone the admin screen uses. Given the +5:30 on order 73, check that the zone is what you expect, since an AUD/Australian shop would normally be +10:30 or +11 in March. `timezone()` needs DuckDB's ICU extension, which ships with the standard builds.

If the file turns out to contain naive UTC with no offset markers, the rows can't be told apart by format. In that case, fix it at the source by re-exporting in one consistent convention, or convert rows from the cutover `id` onwards.

**Rebuild:**

```
dbt seed --full-refresh -s raw_orders     # column type changes
dbt build --full-refresh -s daily_revenue+ # plus upstream; see below
```

`--full-refresh` on `daily_revenue` is required. It is incremental and only rebuilds `max(order_date) - 3 days`, which is now early April. A normal run would leave the wrong 26–28 March rows in place permanently.

Also update the descriptions in `models/schema.yml` from "Date (UTC)" to shop-local for `order_date`, `first_order` and `most_recent_order`.

## 3. Tests that would have caught it

**A format guard on the raw feed**, which would have failed on the first offset row in March. Save it as `tests/assert_raw_orders_ordered_at_format.sql`:

```sql
-- every ordered_at must be in one of the formats stg_orders knows how to interpret
select id, ordered_at
from {{ ref('raw_orders') }}
where not regexp_full_match(
    ordered_at,
    '\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(Z|[+-]\d{2}:?\d{2})?'
)
```

Before the fix, the right version of this test was stricter: it rejected any offset at all, i.e. it used the pattern without the optional suffix. That would have failed loudly the day the export changed.

**A unit test pinning the conversion**, so a regression in the parsing logic is caught. Add it to `models/staging/schema.yml`:

```yaml
unit_tests:
  - name: stg_orders_ordered_at_is_shop_local
    model: stg_orders
    given:
      - input: ref('raw_orders')
        rows:
          - {id: 1, user_id: 1, ordered_at: '2018-03-10 23:30:00', status: completed}
          - {id: 73, user_id: 19, ordered_at: '2018-03-16 01:15:20+05:30', status: completed}
    expect:
      rows:
        - {order_id: 1, order_date: '2018-03-10'}
        - {order_id: 73, order_date: '2018-03-16'}
```

The expected local dates assume `shop_timezone` is the +05:30 zone. Adjust them if it's something else.
