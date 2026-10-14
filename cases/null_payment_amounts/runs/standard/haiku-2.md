claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 46 s | USD 0.008 | 2026-10-14 | project ad0b16b | prompt b1e95fc444b5

## 1. Root cause

**The data is wrong at the source.** Two rows in `raw_payments` have a NULL `amount`:

- `id 30`: order 25, `coupon`, `amount` NULL. Settlement arithmetic points to 1600 cents (16.00): 58.00 settled minus 42.00 shown.
- `id 55`: order 49, `credit_card`, `amount` NULL. This is probably the other 49.00 of the 65.00 gap (1533.00 − 1468.00), but the sample doesn't show that row's value, so confirm it.

**The dbt project turns the NULLs into silent under-counts.** Four places let this through:

- `models/staging/stg_payments.sql`: `amount / 100` passes NULL through unchanged.
- `models/staging/schema.yml`: `stg_payments.amount` has no `not_null` test.
- `models/orders.sql`: `sum(payments.amount)` ignores NULLs. `models/schema.yml` has `not_null` on `orders.amount`, but that test checks the aggregate, which stays non-null whenever an order has at least one non-null payment. Order 25 has a 20.00 payment and a 22.00 payment alongside the NULL one, so its total is non-null and the test passes.
- `models/daily_revenue.sql`: this is why the dashboard looks correct. It is incremental and only rebuilds the last three days, so older dates keep the revenue they had before the NULLs arrived. Monthly differences confirm this: daily_revenue minus orders is 38.00 (Jan), 21.00 (Feb), 6.00 (Mar), totalling 65.00. The dashboard matches settlement because it is a stale snapshot, not because the logic is right.

I haven't run queries here; this is based on the pasted output. To confirm, run:

```sql
select p.id, p.order_id, p.payment_method, p.amount
from raw_payments p
where p.amount is null;
```

Then check the processor settlement for those two payments and the ingestion job's logs for the load that wrote them. The NULLs may come from a feed that sent a non-integer or non-numeric value into the INTEGER column. That is an ingestion question, which I can't answer from the project files.

## 2. Fix

**Data (upstream, ingestion owns this):** reload or patch `raw_payments` ids 30 and 55 with the processor's amounts in cents. Don't fill them with 0 in dbt, since that would hide the same problem.

**Code (dbt):**

`models/daily_revenue.sql`: the table has about 69 rows, so a full rebuild is cheap and removes the stale-snapshot trap:

```sql
{{
    config(
        materialized='table'
    )
}}

with orders as (

    select * from {{ ref('orders') }}

),

final as (

    select
        order_date,
        count(*) as orders,
        sum(amount) as revenue

    from orders

    group by order_date

)

select * from final
```

Keeping it incremental would also need `dbt build --full-refresh -s daily_revenue` after every raw correction, so the table form is the better choice unless the table grows a lot.

After the raw fix and rebuild, Jan–Mar should show orders at 1533.00, matching settlement and daily_revenue.

## 3. Test that would have caught it

Add a `not_null` test on the staging amount, which fails at the first point a NULL appears:

`models/staging/schema.yml`:
```yaml
  - name: stg_payments
    columns:
      - name: payment_id
        tests:
          - unique
          - not_null
      - name: payment_method
        tests:
          - accepted_values:
              arguments:
                values: ['credit_card', 'coupon', 'bank_transfer', 'gift_card']
      - name: amount
        tests:
          - not_null
```

Add a reconciliation test so the warehouse must agree with the payments it was built from. `tests/assert_orders_reconcile_to_payments.sql`:

```sql
with totals as (

    select
        (select sum(amount) from {{ ref('stg_payments') }}) as payments_total,
        (select sum(amount) from {{ ref('orders') }}) as orders_total

)

select *
from totals
where abs(payments_total - orders_total) > 0.005
```

This would have failed on the 65.00 gap even if the `not_null` test were missing. Before merging, check for payments whose `order_id` has no matching order, since the `left join` from orders would drop them and make this test fail for a different reason.

**Other things I noticed (not part of this bug):** `models/schema.yml` documents `customers.total_order_amount`, which the `customers` model doesn't produce, so dbt won't test it. The `region_revenue` joins also deserve a check for fan-out if `raw_customer_regions` ever holds more than one row per customer.
