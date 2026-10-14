claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 38 s | USD 0.198 | 2026-10-15 | project ad0b16b | prompt 75e42f540dd8

## 1. Root cause

**The data in `raw_payments` is wrong, not the SQL.** Starting in mid-March, the ingestion job loaded `bank_transfer` rows into `raw_payments.amount` at **10,000× the scale of every other row**. All other rows are in cents. These rows look like millionths of a dollar (micro-units), which suggests the ANZ feed changed its amount unit. `models/staging/stg_payments.sql` assumes cents (`amount / 100`), so each bad row comes out 10,000× too large. `orders`, `customers` and `region_revenue` then sum it, and `fees` multiplies it by 0.005.

The evidence fits exactly:

| | Raw `amount` | `stg_payments` | Should be |
|---|---|---|---|
| Order 73 | 3,000,000 | 30,000.00 | 3.00 (300 cents) |
| Order 57 (1 Mar, OK) | 200 | 2.00 | 2.00 |
| Payment 105 / order 92 | 15,000,000 | 150,000.00 | 15.00 |

- **April:** 390,000 = 39 × 10,000. Every April bank transfer is affected, and the statement says 39.00.
- **March:** if 48.00 is correct and 114.00 is scaled, then 48 + 114 × 10,000 = 1,140,048, and 48 + 114 = 162. This matches the statement exactly. So the switch happened partway through March, and early-March rows are fine.
- **Other methods:** credit card, coupon and gift card totals look normal, so only `bank_transfer` is affected.

**Why every test passed:** the only checks on `amount` are `not_null`. Nothing checks range, scale, or reconciliation.

**Second problem, in `models/daily_revenue.sql`:** this model is incremental and only rebuilds the last 3 days, so it now holds a mix of stale and fresh rows. March shows 622.00, which happens to be the correct figure because those days were built before the bad rows arrived. April shows 240,115.00, which is wrong and also doesn't match `orders` (390,100). Fixing the source alone won't repair the older days, because the 3-day window never revisits them.

## 2. Fix

**Preferred fix: correct the data at the source.** Have the ingestion team reload the affected `raw_payments` rows in cents, or normalise the unit in the loader. First, find where the bad rows start:

```sql
select id, order_id, amount, o.ordered_at
from raw_payments p join raw_orders o on o.id = p.order_id
where payment_method = 'bank_transfer'
order by o.ordered_at;
```

The bad rows begin where `amount` jumps to millions. Each should be divided by 10,000.

**Stopgap until the source is fixed:** normalise in `stg_payments.sql`, keyed on the cutoff found above, not on how big the amount is:

```sql
        -- `amount` is currently stored in cents, so we convert it to dollars.
        -- From payment id {{ var('bank_transfer_micros_from_id') }} the bank transfer feed
        -- sends amounts in millionths of a dollar instead of cents.
        case
            when payment_method = 'bank_transfer'
             and id >= {{ var('bank_transfer_micros_from_id') }}
            then amount / 1000000
            else amount / 100
        end as amount
```

Set the variable in `dbt_project.yml` under `vars:` once you know the first bad id.

**Then rebuild `daily_revenue` from scratch**, or its April rows stay wrong:

```
dbt build --full-refresh
```

`dbt build -s daily_revenue --full-refresh` is enough if the upstream models are already rebuilt.

## 3. Tests that would have caught it

`tests/assert_payment_amounts_plausible.sql` flags any single payment outside a sane range. Real payments here are under about 30 AUD, so a 1,000 AUD cap is generous:

```sql
select *
from {{ ref('stg_payments') }}
where amount <= 0 or amount > 1000
```

`tests/assert_daily_revenue_reconciles_to_orders.sql` catches incremental drift like the April mismatch above:

```sql
with d as (select order_date, sum(revenue) as revenue from {{ ref('daily_revenue') }} group by 1),
     o as (select order_date, sum(amount)  as revenue from {{ ref('orders') }}        group by 1)
select *
from o full outer join d using (order_date)
where abs(coalesce(o.revenue, 0) - coalesce(d.revenue, 0)) > 0.01
```

The first test would have failed on the first nightly run after the feed changed. The second would have failed the following night.

A small unrelated issue: `models/schema.yml` documents `customers.total_order_amount`, but the model outputs `customer_lifetime_value`.
