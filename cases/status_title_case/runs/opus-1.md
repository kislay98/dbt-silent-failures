claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 20 s | USD 0.112 | 2026-10-14 | project f769fdf | prompt 2b73e24d3b63

## 1. Root cause

The upstream orders feed started sending **capitalized `status` values** partway through February 2018: `Completed`, `Placed`, `Shipped`, and `Return_pending`. January has only lowercase values. February is mixed, with 12 `completed` and 14 `Completed`. From March on, every value is capitalized.

The bad data comes in through the `raw_orders` seed (`jaffle_shop/seeds/raw_orders.csv`). `jaffle_shop/models/staging/stg_orders.sql` passes `status` through unchanged.

`jaffle_shop/models/customers.sql` then uses a case-sensitive comparison:

```sql
sum(case when orders.status = 'completed' then amount else 0 end) as completed_amount
```

This only counts the 36 lowercase `completed` orders, which total **604.00**. It silently drops the 31 `Completed` orders, which total **499.00**. 604 + 499 = **1103.00**, which matches the finance ledger exactly. `sum(orders.amount)` is still 1672.00 because no filter on status is involved there.

**Why every test passed:** the `accepted_values` tests on `status` were widened to accept the capitalized variants. This happened in both `jaffle_shop/models/staging/schema.yml` and `jaffle_shop/models/schema.yml`, with `'Placed', 'Shipped', 'Completed', 'Return_pending', 'Returned'` added to the lists. The test that should have caught the format change was edited to accept it, and none of the downstream logic was updated to match.

## 2. Fix

Normalize the value once in staging so every downstream model sees a single canonical form.

`jaffle_shop/models/staging/stg_orders.sql`:

```sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,
        ordered_at,
        cast(ordered_at as date) as order_date,
        lower(trim(status)) as status

    from source

)
```

After a rebuild, `customers.completed_lifetime_value` sums to 1103.00, and `orders` shows one row per status when grouped.

If you control the seed, you can also lowercase the values in `raw_orders.csv`. Keep the staging normalization either way, since this is a real feed that may change again.

## 3. Tests that would have caught it

**a) Restore strict `accepted_values`** in both schema files and remove the capitalized variants:

```yaml
      - name: status
        tests:
          - accepted_values:
              arguments:
                values: ['placed', 'shipped', 'completed', 'return_pending', 'returned']
```

On `stg_orders`, this test would have failed on the very first build after the feed changed. With the `lower()` fix in place it passes. If the feed introduces a genuinely new status, the test will fail again, and it should.

**b) Add a reconciliation test** so a metric can't drift from its source unnoticed, whatever the cause. Create `jaffle_shop/tests/assert_completed_ltv_reconciles_to_orders.sql`:

```sql
-- completed revenue on customers must equal completed revenue on orders
with c as (
    select coalesce(sum(completed_lifetime_value), 0) as amt from {{ ref('customers') }}
),
o as (
    select coalesce(sum(amount), 0) as amt from {{ ref('orders') }}
    where lower(status) = 'completed'
)
select c.amt as customers_completed, o.amt as orders_completed
from c cross join o
where abs(c.amt - o.amt) > 0.005
```

On this week's data, this test returns 604 vs 1103 and fails the build.

A small related issue: `models/schema.yml` documents a `total_order_amount` column on `customers`, but the model actually outputs `customer_lifetime_value`. You may want to fix that while you're editing the file.
