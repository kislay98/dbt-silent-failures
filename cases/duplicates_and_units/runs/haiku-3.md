claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 61 s | USD 0.006 | 2026-10-14 | project f769fdf+wk6ext | prompt 3077d0cba2a2

## 1. Root cause

Both problems enter in the raw payments data and are passed through `stg_payments.sql`. From there they flow into `orders.sql`, `customers.sql`, `region_revenue.sql`, and `daily_revenue.sql`. The downstream SQL is doing the right arithmetic on bad input.

**a) Duplicate payments in `raw_payments`.** Payment ids 114, 115, 116, and 121 repeat payments 1, 11, 21, and 71 with the same `order_id`, `payment_method`, and `amount`. They look like a replayed batch appended with new surrogate ids. This is why order 1 shows 20.00 when the customer was charged 10.00: payments 1 and 114 are both 10.00. Order 62's gift card payment of 0.14 also appears twice (71 and 121).

The tests missed it because `unique` and `not_null` on `payment_id` pass when the ids differ. Nothing checks the business key or reconciles to the processor.

**b) Gift card amounts are in dollars, but staging divides everything by 100.** `stg_payments.sql` does `amount / 100` for every method, which assumes cents. Coupons match finance exactly, so coupon rows are in cents. The gift card numbers point to a different unit:

- Staging's gift card total is 2.19 = (205 + 14) / 100, where 14 is the duplicate above.
- Remove the duplicate and the raw values sum to 205 in whole units, which matches the redemption statement exactly.

So the gift card feed is in dollars while the card feeds are in cents, and the blanket `/ 100` shrinks gift cards by 100x. This is inferred from the numbers. I haven't seen the raw gift card rows or the feed documentation, so confirm it before you rely on it.

The credit card gap (109) and bank transfer gap (57) are consistent with more duplicates than the sample shows. I can't confirm the residual without the full table.

**Downstream effects:** `orders.amount` and the per-method columns are wrong, so `customers.customer_lifetime_value`, `region_revenue`, and `daily_revenue` are also wrong. `daily_revenue` is incremental, so its three-day rebuild window won't correct older days.

## 2. Fix

**a) Ingestion (the real fix).** Make the load idempotent by deduplicating on the processor's transaction reference before insert. The raw columns you showed (`id`, `order_id`, `payment_method`, `amount`) don't include one, so the ingestion job will need to supply it. Also confirm that the unit for each feed is recorded.

**b) Staging (defensive, after you confirm the duplicates are true replays).** Replace `stg_payments.sql`:

```sql
with source as (

    {#-
    Normally we would select from the table here, but we are using seeds to load
    our data in this project
    #}
    select * from {{ ref('raw_payments') }}

),

deduped as (

    select
        *,
        row_number() over (
            partition by order_id, payment_method, amount
            order by id
        ) as row_num
    from source

),

renamed as (

    select
        id as payment_id,
        order_id,
        payment_method,

        -- card and bank feeds are in cents; the gift card feed is in dollars
        case when payment_method = 'gift_card' then amount else amount / 100.0 end as amount

    from deduped
    where row_num = 1

)

select * from renamed
```

Before merging, check two things:

- The partition-by dedupe also drops genuine identical split payments. Run this to see what it would remove:
  ```sql
  select order_id, payment_method, amount, count(*) as n, min(id), max(id)
  from raw_payments
  group by all
  having count(*) > 1;
  ```
- The hard-coded `'gift_card'` should become a unit column on `raw_payment_methods` once you've confirmed the unit for each processor.

Also add a `unique` test on `raw_payment_methods.payment_method` in `models/schema.yml`. Right now only `not_null` is there, and if that seed has two rows for one method, the join in `orders.sql` fans out every payment.

**c) Rebuild, including the history.** `daily_revenue` won't self-correct, so use a full refresh:

```bash
dbt build --full-refresh -s stg_payments+
```

**Expected totals after the fix** (should match finance): credit card 871.00, bank transfer 411.00, gift card 205.00, coupon 185.00.

## 3. Test that would have caught it

Reconcile the staging totals to the processor settlement per payment method. This catches both the duplicates and the unit error. A simple range test would not catch the gift card problem, since 0.23 is a plausible amount.

This needs the processor settlement totals loaded as a source or seed. I've used the placeholder `raw_processor_settlements` with columns `payment_method` and `settled_amount`, so adjust the name to match what you have.

`tests/assert_payments_reconcile_to_processor.sql`:

```sql
with ours as (

    select payment_method, round(sum(amount), 2) as total
    from {{ ref('stg_payments') }}
    group by payment_method

),

processor as (

    select payment_method, round(sum(settled_amount), 2) as total
    from {{ ref('raw_processor_settlements') }}
    group by payment_method

)

select
    coalesce(ours.payment_method, processor.payment_method) as payment_method,
    ours.total as our_total,
    processor.total as processor_total
from ours
full outer join processor
    on ours.payment_method = processor.payment_method
where ours.total is distinct from processor.total
```

A singular test fails when it returns any rows, so this fails on the current data and passes after the fix.

Without a settlement table, the weaker fallback is `dbt_utils.unique_combination_of_columns` on `(order_id, payment_method, amount)` in `stg_payments`, set to `severity: warn`. It would also flag legitimate identical split payments, so treat it as a signal to investigate, not a hard failure.

**Other issue spotted (not part of the finance report):** `raw_customer_regions` has an `updated_at` column, which suggests multiple rows per customer over time. `region_revenue.sql` joins payments to that table, so any customer with more than one region row will have their revenue counted more than once. Add a `unique` test on `customer_id` in that seed, or pick the latest row per customer before joining.
