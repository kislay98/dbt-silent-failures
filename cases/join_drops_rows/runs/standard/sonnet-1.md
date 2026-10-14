claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 36 s | USD 0.110 | 2026-10-14 | project ad0b16b | prompt 3fdc8e98d7e9

## 1. Root cause

**The payment rows in `seeds/raw_payments.csv` are missing their `order_id` (NULL), so they are silently dropped by every downstream join.**

I can't see the seed file, so this is inferred from the evidence:

- `raw_payments` row `id=91` (credit_card, 900) has `order_id = NULL`. `stg_payments` row `payment_id=15` (bank_transfer, 14.00) also has `order_id = NULL`. These are the orphans.
- `orders.sql` and `region_revenue.sql` both join payments on `order_id`, so NULL-keyed payments match nothing. In `customers.sql` they fall into a `NULL` customer group that the left join from `customers` never picks up.
- That explains 1672 − 1532 = **140.00** missing from `orders`, `customers` (1532) and `region_revenue` (1532). Order 25's missing 16.00 is presumably one of these orphans.
- No test catches it. `stg_payments.order_id` has no `not_null` or `relationships` test, and every existing test checks a column that is still populated after the drop. All 44 tests pass.

`daily_revenue` is a second problem. It sums to **1672.00** (496 + 415 + 622 + 139), not 1532, while its order counts match `orders`. It is stale. It is incremental and rebuilds only the last 3 days relative to `max(order_date)`, so older days keep the values from an earlier run, when the payments still had their `order_id`. That also means three of your four outputs disagree with the fourth, and `daily_revenue` is currently the only evidence of where the orphans belonged.

## 2. Fix

**Before anything else, don't `--full-refresh` yet.** Snapshot the old `daily_revenue`, because it tells you which dates the orphans belong to:

```sql
create table daily_revenue_old as select * from daily_revenue;

select d.order_date, d.revenue - coalesce(sum(o.amount), 0) as missing
from daily_revenue_old d
left join orders o using (order_date)
group by d.order_date, d.revenue
having abs(d.revenue - coalesce(sum(o.amount), 0)) > 0.001;
```

To list the orphans:

```sql
select * from stg_payments where order_id is null
   or order_id not in (select order_id from stg_orders);
```

Then:

1. **Data fix:** restore the correct `order_id` on those rows in `raw_payments.csv` from the source system. Use the date diff above to check candidates. I can't derive the IDs from what you pasted, so don't guess them. If some are genuinely unattributable, route them to an explicit `unassigned_payments` model or reconciliation line instead of dropping them.
2. **Fix `daily_revenue`:** it is tiny, so make it a plain `table`. If it must stay incremental, use `on_schema_change` plus a lookback that covers corrections, and run `dbt build --full-refresh -s daily_revenue` after the data fix. A 3-day window can never restate older orders.
3. **Rebuild:** `dbt build`. All four totals should then be 1672.00.

## 3. Tests that would have caught it

In `models/staging/schema.yml`:

```yaml
      - name: stg_payments
        columns:
          - name: order_id
            tests:
              - not_null
              - relationships:
                  arguments:
                    to: ref('stg_orders')
                    field: order_id
```

A reconciliation test, `tests/assert_revenue_reconciles.sql`. It also catches the stale incremental table:

```sql
with src as (select sum(amount) as total from {{ ref('stg_payments') }})

select 'orders' as model, src.total, (select sum(amount) from {{ ref('orders') }}) as model_total from src
union all
select 'customers', src.total, (select sum(customer_lifetime_value) from {{ ref('customers') }}) from src
union all
select 'region_revenue', src.total, (select sum(revenue) from {{ ref('region_revenue') }}) from src
union all
select 'daily_revenue', src.total, (select sum(revenue) from {{ ref('daily_revenue') }}) from src
-- the select list above returns every model; the where below keeps only mismatches
```

Wrap the union in a subquery and filter on `abs(total - model_total) > 0.005` so that it only returns failing rows. dbt fails a singular test when it returns any rows.
