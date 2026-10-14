claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 59 s | USD 0.010 | 2026-10-15 | project ad0b16b | prompt 9fc7feb7aef0

## 1. Root cause

**`jaffle_shop/models/staging/stg_orders.sql`** derives `order_date` with `cast(ordered_at as date)`. That is correct only if every `ordered_at` value is in the same zone as the reporting convention. The data breaks that assumption.

The seed `raw_orders` (the `ordered_at` column, `TIMESTAMP` with no zone) mixes two conventions:

- **Before the cutover (early/mid-March 2018):** `ordered_at` is already India local time. Its calendar date matches the admin screen, which is why the two always agreed.
- **After the cutover:** `ordered_at` is UTC. An order placed between 18:30 and 23:59 UTC is still on the same UTC date, but on the next calendar day in IST. The admin screen shows it on the next day, and `orders` shows it on the previous day.

The sample rows confirm the post-cutover direction:

- `raw_orders` id 73, `2018-03-15 19:45:20`. In IST that is 2018-03-16 01:15. The admin screen has 1 order on 03-16 and none on 03-15, which matches.
- `raw_orders` id 92, `2018-04-01 20:55:29`. In IST that is 2018-04-02 02:25. The admin screen has 1 order on 04-02 and none on 04-01, which matches.

Each day-level difference is an order shifted by 5h30m across midnight, so the month totals still match. That is why the existing checks never failed: the tests check uniqueness, not-null and accepted values, and the monthly sums are unchanged.

`daily_revenue.sql` inherits the same wrong dates. Its incremental filter only rebuilds the last three days, so March rows will not correct themselves.

**Limits of this diagnosis:** I don't have the seed CSV or the full per-day warehouse counts, so I can't run the complete diff. The cutover date is inferred from the sample rows: it is on or before 2018-03-15. Confirm the exact boundary with the source system before you hardcode it (step 2). Run this to check every row against the admin counts:

```sql
select order_id, ordered_at,
       cast(ordered_at as date) as warehouse_date,
       cast(ordered_at + interval '5 hours 30 minutes' as date) as ist_date
from raw_orders
where ordered_at >= timestamp '2018-03-01'
order by ordered_at;
```

## 2. Fix

Normalise the mixed convention in staging and derive `order_date` in the business timezone, since ops reconcile in local time. Change `stg_orders.sql`:

```sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,
        ordered_at,

        -- raw feed is IST before the cutover and UTC after it; order_date is the IST calendar date
        cast(
            case
                when ordered_at < timestamp '2018-03-15 00:00:00'
                    then ordered_at                                    -- already IST
                else ordered_at + interval '5 hours 30 minutes'        -- UTC -> IST (Asia/Kolkata)
            end
        as date) as order_date,

        status

    from source

)
```

Then:

- **Confirm the cutover** with the source system. Change the constant if the switch happened at a different time.
- **Full-refresh `daily_revenue`**: `dbt run --select daily_revenue --full-refresh`. The incremental lookback will not fix March otherwise.
- **Update the docs** in `models/schema.yml`. `orders.order_date`, `customers.first_order` and `customers.most_recent_order` currently say "Date (UTC)". They should say "Date (Asia/Kolkata)".
- **Longer term**, have the feed emit `timestamptz` (or always UTC) so the seed doesn't carry two conventions.

## 3. Test that would have caught it

Reconcile against the number ops already check. Save the admin export as a seed, then add a singular test that fails on any mismatch.

`seeds/admin_daily_order_counts.csv`:

```csv
order_date,orders
2018-03-01,2
2018-03-02,1
...
2018-04-09,1
```

`tests/assert_orders_match_admin_daily_counts.sql`:

```sql
with expected as (

    select cast(order_date as date) as order_date, orders
    from {{ ref('admin_daily_order_counts') }}

),

actual as (

    select order_date, count(*) as orders
    from {{ ref('orders') }}
    where order_date between date '2018-03-01' and date '2018-04-09'
    group by order_date

)

select
    coalesce(expected.order_date, actual.order_date) as order_date,
    expected.orders as admin_orders,
    actual.orders as warehouse_orders

from expected
full outer join actual
    on expected.order_date = actual.order_date

where expected.orders is distinct from actual.orders
```

Before the staging fix, this test returns the shifted days (for example 03-15/03-16 and 04-01/04-02), so it would have failed on the first build. The date range is fixed to the export window so that orders outside it don't produce false failures.
