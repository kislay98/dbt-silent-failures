claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 41 s | USD 0.207 | 2026-10-15 | project ad0b16b | prompt 75e42f540dd8

## 1. Root cause

**The data in `raw_payments` is wrong: bank transfer amounts from mid-March onward are 10,000× too large.** `stg_payments.sql` divides every amount by 100 because it expects cents. From some point in mid-March, the `bank_transfer` rows (ANZ feed) arrived in a unit 100× smaller than cents (1/10,000 of a dollar). After the `/ 100`, those rows come out 10,000× too big. The model code is not at fault. It is applying a correct rule to data that broke the rule.

The numbers match a factor of exactly 10,000:

| | Statement | `orders` | Check |
|---|---|---|---|
| Order 73 | 3.00 | 30,000.00 | raw `3000000` instead of `300` |
| April bank transfers | 39.00 | 390,000.00 | 39 × 10,000 |
| March bank transfers | 162.00 | 1,140,048.00 | 48 correct (early March) + 114 × 10,000 = 1,140,048 |
| Order 92 (raw) | — | 15,000,000 | should be 1,500 (15.00) |
| Order 97 (raw) | — | 14,000,000 | should be 1,400 (14.00) |

The same bad rows spread to everything built from `stg_payments`:
- `orders.amount`, `bank_transfer_amount` and `fees` (bank transfer fee of 0.005 × 1.14M ≈ 5,700)
- `customers.customer_lifetime_value` and `completed_lifetime_value`
- `region_revenue`

**`daily_revenue` is wrong in a different way.** It is incremental and only rebuilds the last three days. Its March figure of 622 is correct only because those days were built before the bad rows landed. April (240,115) holds a mix of good and bad days, and it no longer agrees with `orders`.

**Why every test passed:** all tests check `not_null`, `unique` and `accepted_values`. Nothing checks the size of an amount or reconciles one model against another.

## 2. Fix

**Preferred: fix it at the source.** Ask the ingestion team to reload the affected `bank_transfer` rows in cents, and correct the ANZ loader so it converts to cents.

**First, find the cutover.** `raw_payments` has no load timestamp, so check where the jump happens:

```sql
select p.id, p.order_id, o.ordered_at, p.amount
from raw_payments p
join raw_orders o on o.id = p.order_id
where p.payment_method = 'bank_transfer'
order by o.ordered_at;
```

**Stopgap until the reload, in `models/staging/stg_payments.sql`.** Key the guard on the confirmed cutover. Don't use a size threshold alone: a real 100.00 payment is `10000` cents and would look the same as a bad 1.00 payment.

```sql
renamed as (

    select
        id as payment_id,
        order_id,
        payment_method,

        -- `amount` is currently stored in cents, so we convert it to dollars.
        -- ANZ bank transfers loaded from payment <first_bad_id> onward are in 1/10000 dollars; remove once re-ingested
        case
            when payment_method = 'bank_transfer' and id >= {{ var('anz_unit_cutover_payment_id') }}
                then amount / 10000
            else amount / 100
        end as amount

    from source

)
```

**Then rebuild, including a full refresh of the incremental model.** Without `--full-refresh`, `daily_revenue` keeps its bad April days:

```
dbt build --full-refresh -s stg_payments+
```

Afterwards, bank transfers should total 162.00 for March and 39.00 for April, and order 73 should show 3.00.

## 3. Tests that would have caught this

**Plausible amounts per payment** (`tests/assert_payment_amounts_plausible.sql`). Set the cap to fit the business; the largest real order here is under 100.

```sql
-- Catches unit or scale errors from a feed
select payment_id, order_id, payment_method, amount
from {{ ref('stg_payments') }}
where amount < 0 or amount > 1000
```

**Incremental model agrees with its source** (`tests/assert_daily_revenue_matches_orders.sql`). This catches `daily_revenue` drifting from `orders` when data changes outside the three-day window.

```sql
with expected as (
    select order_date, count(*) as orders, sum(amount) as revenue
    from {{ ref('orders') }}
    group by order_date
)
select e.order_date, e.revenue as expected_revenue, d.revenue as actual_revenue
from expected e
full outer join {{ ref('daily_revenue') }} d on e.order_date = d.order_date
where d.order_date is null or e.order_date is null
   or e.orders <> d.orders
   or abs(coalesce(e.revenue, 0) - coalesce(d.revenue, 0)) > 0.01
```

If you have `dbt_utils`, you can use `accepted_range` (with `min_value: 0`, `max_value: 1000`) on `stg_payments.amount` and `orders.amount` instead of the first singular test. The strongest check would be a monthly reconciliation of per-method totals against the processor statement, if that statement can be loaded as a source.
