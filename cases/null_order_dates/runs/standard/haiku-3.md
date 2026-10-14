claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 38 s | USD 0.007 | 2026-10-14 | project ad0b16b | prompt 3b339a6e8808

## 1. Root cause

The March gap is exactly the 8 orders with no date. The `NULL` month row in the `orders` output has 8 orders and 149.00, and 622.00 − 473.00 = 149.00 with 35 − 27 = 8. Two separate problems combine to produce this.

**a) Bad upstream data (`raw_orders`, loaded by the ingestion job).** Eight rows have `ordered_at IS NULL`. For example, raw order id 70 (customer 8) and id 69 (customer 3). `stg_orders.sql` casts that to a `NULL` `order_date`, so `orders` carries 8 undated orders. Those orders have no `order_date`, so they don't appear in any month in `orders`. That also explains the two March customers with no `first_order`: `customers.first_order` is `min(order_date)`, which ignores `NULL`s, so a customer whose only orders are undated gets `NULL`.

**b) Stale rows in `daily_revenue.sql`.** The `orders` model no longer dates those 8 orders, yet `daily_revenue` still shows 35 orders and 622.00 for March. The incremental logic explains why:

- Each run only rebuilds `order_date >= max(order_date) - 3 days`. March is never in that window, so March rows are never recomputed.
- The model never deletes rows that no longer exist in its source. The March rows were written while those orders still had March dates. When the ingestion job later nulled `ordered_at`, the old March totals stayed.
- Undated orders (`NULL` `order_date`) also fall outside the `>=` filter, so they are never picked up either.

The data shows the symptom and the arithmetic points to the cause. I can't see the build history, so the "they were dated in March before" part is inferred. Checking that is cheap: compare `daily_revenue` for March with `orders` grouped by date, and the mismatch should be exactly the 8 undated orders.

The `not_null` tests passed because `daily_revenue` has no `NULL` `order_date` row. The stale March rows are non-null, and nothing reconciles `daily_revenue` against `orders`.

## 2. Fix

**Step 1: correct the source.** Get the 8 `ordered_at` values restored in `raw_orders` from the upstream system, probably a timestamp parsing or timezone problem in the ingestion job. Finance needs to confirm the correct dates. If some orders truly have no date, decide explicitly how to treat them rather than letting them drop out silently.

**Step 2: rebuild `daily_revenue` from scratch** so the stale March rows are removed:

```
dbt build --select daily_revenue --full-refresh
```

Expect a caveat: a full refresh also reads undated orders, so until Step 1 is done, `daily_revenue` will get a `NULL` `order_date` group and `not_null_daily_revenue_order_date` will fail. That failure is correct and is the signal you want. If the dates can't be recovered, add an explicit `where order_date is not null` to `daily_revenue.sql` and document the exclusion.

**Step 3 (recommended): make the incremental model self-correcting.** A window that never revisits older dates will always be fragile. Either rebuild on a schedule with `--full-refresh` (the table is small), or widen the window so it is driven by the earliest changed date, not just the last three days.

## 3. Tests that would have caught this

**a) `not_null` on the source dates** (would have failed the nightly build on the 8 `NULL` rows). Add to `models/schema.yml`:

```yaml
  - name: orders
    columns:
      - name: order_date
        tests:
          - not_null
```

Add the same to `stg_orders.ordered_at` in `models/staging/schema.yml`.

**b) A reconciliation test** (catches stale or drifting `daily_revenue` rows, which the existing tests cannot see). Create `tests/daily_revenue_matches_orders.sql`:

```sql
with expected as (

    select
        order_date,
        count(*) as orders,
        round(sum(amount), 2) as revenue

    from {{ ref('orders') }}
    where order_date is not null
    group by order_date

),

actual as (

    select
        order_date,
        orders,
        round(revenue, 2) as revenue

    from {{ ref('daily_revenue') }}

)

select
    coalesce(expected.order_date, actual.order_date) as order_date,
    expected.orders as expected_orders,
    actual.orders as actual_orders,
    expected.revenue as expected_revenue,
    actual.revenue as actual_revenue

from expected
full outer join actual
    on expected.order_date = actual.order_date

where expected.orders is distinct from actual.orders
   or expected.revenue is distinct from actual.revenue
```

This returns a row for every date where `daily_revenue` disagrees with `orders`, including dates that exist only in `daily_revenue`. It would have failed on the 2018-03 stale rows, and it is the test that protects finance's number directly.

**Other things I noticed (not causing this issue):**
- `customers.yml` documents `total_order_amount`, which the model doesn't produce. The docs drift from the SQL.
- `stg_customers` and `stg_orders` comments still say the data comes from seeds. The raw tables are now loaded by the ingestion job.
- `daily_revenue` takes revenue from `orders`, which joins to payments at build time. Payments that land after the 3-day window will leave revenue understated for those dates, which is a second reason the window needs revisiting.
