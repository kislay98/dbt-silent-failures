claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 43 s | USD 0.211 | 2026-10-14 | project ad0b16b | prompt 3b4c114442ec

# Root cause: duplicate orders and payments re-sent into the raw tables

The models are not the problem. The data in **`raw_orders`** and **`raw_payments`** is (`seeds/raw_orders.csv` and `seeds/raw_payments.csv`, or wherever the ingestion job writes them).

**The ingestion job re-sent 16 February orders with new ids**, along with their payments. Because the copies have fresh primary keys, every `unique` and `not_null` test passes.

The evidence:

| | admin / processor | `orders` | difference |
|---|---|---|---|
| orders | 99 | 115 | **16** |
| amount | 1672.00 | 1913.00 | **241.00** |
| Feb orders (`daily_revenue` vs `orders`) | 27 | 43 | **16** |
| Feb revenue | 415.00 | 656.00 | **241.00** |

- The whole discrepancy is in February. January, March and April match exactly.
- `raw_orders` holds ids up to 115, but the admin has 99 orders. Every id > 99 in the sample is dated 10–23 Feb (101, 104, 105, 106, 109, 110, 112, 113, 114).
- One pair is an exact copy. In `stg_orders`, **order 105 and order 41** both have customer 99, `2018-02-14 10:44:12` and status `completed`.
- Customer 51 has order 114 (`2018-02-23 18:53:27`), a copy of one of their real orders. Two such copies explain why they show 5 orders instead of 3.
- `raw_payments` has 131 rows. The 18 payments with ids 114–131 belong to orders 100–115 (for example, payment 131 → order 115 and 127 → order 113). So the payments were re-sent as well, and that is where the extra 241.00 comes from.

`daily_revenue` "still shows 99 / 1672.00" only because it is incremental. The copies were back-dated to February, which is outside its 3-day rebuild window, so it never picked them up. It is right by accident. This also exposes a second, latent bug: the model can never absorb late-arriving data that is older than 3 days.

To confirm before deleting anything (I can't see the seed data, so the id ranges above are inferred from the samples):

```sql
-- expect 16 rows, dup_id 100..115, all in Feb 2018
select o.id as original_id, d.id as dup_id, d.user_id, d.ordered_at, d.status
from raw_orders o
join raw_orders d
  on d.user_id = o.user_id and d.ordered_at = o.ordered_at and d.status = o.status and d.id > o.id
order by d.id;

-- expect the duplicate orders' payments to mirror the originals
select p.order_id, sum(p.amount) / 100 as amount
from raw_payments p where p.order_id > 99 group by 1 order by 1;
```

# Fix

**1. Data fix (primary).** Remove the re-sent rows and get the ingestion job fixed so it upserts on the source key instead of minting new ids:

```sql
delete from raw_payments where order_id in (/* dup_ids from the query above */);
delete from raw_orders   where id       in (/* dup_ids from the query above */);
```

If these are seed CSVs, delete the same rows from the files and run `dbt seed`.

**2. Defensive guard in staging**, so a future re-send can't inflate counts.

`models/staging/stg_orders.sql`:
```sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,
        ordered_at,
        cast(ordered_at as date) as order_date,
        status

    from source

    -- the ingestion job has re-sent orders under new ids; keep the first copy
    qualify row_number() over (partition by user_id, ordered_at order by id) = 1

)
```

`models/staging/stg_payments.sql`: drop payments that belong to dropped duplicate orders.
```sql
    from source
    where order_id in (select order_id from {{ ref('stg_orders') }})
```

**3. Rebuild the incremental model** so it reflects whatever the source now contains:

```
dbt build --full-refresh -s daily_revenue
```

The 3-day lookback in `daily_revenue` will keep missing back-dated corrections. Either schedule a periodic `--full-refresh`, or widen the window (e.g. 35 days). The table is tiny, so you could also make it a plain `table`.

# Tests that would have caught this

`tests/assert_orders_have_no_duplicates.sql`: no two orders share a natural key.
```sql
select customer_id, ordered_at, count(*) as copies
from {{ ref('stg_orders') }}
group by 1, 2
having count(*) > 1
```

`tests/assert_daily_revenue_matches_orders.sql`: the incremental model reconciles with its source. This would have flagged the February drift.
```sql
with o as (
    select order_date, count(*) as orders, sum(amount) as revenue
    from {{ ref('orders') }} group by 1
)
select coalesce(o.order_date, d.order_date) as order_date, o.orders, d.orders as dr_orders, o.revenue, d.revenue as dr_revenue
from o
full outer join {{ ref('daily_revenue') }} d on o.order_date = d.order_date
where o.orders is distinct from d.orders
   or abs(coalesce(o.revenue, 0) - coalesce(d.revenue, 0)) > 0.005
```

(If you use `dbt_utils`, the first test can be `unique_combination_of_columns: [customer_id, ordered_at]` on `stg_orders`.) Note that a `relationships` test from `stg_payments.order_id` to `stg_orders.order_id` would *not* have caught this, because the duplicate payments point at duplicate orders that exist.

One unrelated nit: `models/schema.yml` documents `customers.total_order_amount`, but the model outputs `customer_lifetime_value`.
