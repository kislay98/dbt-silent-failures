claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 32 s | USD 0.144 | 2026-10-14 | project 8ee5734 | prompt 2e932ff37d63

## 1. Root cause

**The upstream feed renamed `payment_method` to `method`, and `models/staging/stg_payments.sql` still reads only the old column.**

`raw_payments` now has both columns:

```
raw_payments │ payment_method │ VARCHAR
raw_payments │ amount         │ INTEGER
raw_payments │ method         │ VARCHAR   ← new, last column
```

From roughly 2018‑03‑01 (payment_id 65 onward), the ingestion job writes the method into `method` and leaves `payment_method` NULL. `stg_payments.sql` selects `payment_method`, so every March and April payment has `payment_method = NULL` in `stg_payments`. The NULLs then flow through two places:

- **`models/orders.sql`**: each `sum(case when payments.payment_method = '...' then amount else 0 end)` evaluates to 0, but `sum(payments.amount)` still counts the payment. So `amount` is correct (708.00 / 146.00) while every per-method column is 0.00. `fees` is also understated: the join to `raw_payment_methods` misses, and `coalesce(fee_rate, 0)` turns the fee into 0.
- **`models/customers.sql`, `region_revenue.sql`, `daily_revenue.sql`** only use `amount`, so they look fine. That is why only the payment-mix report is visibly wrong.

**Why all 35 checks passed:**
- `accepted_values` on `stg_payments.payment_method` compiles to `where payment_method not in (...)`. NULL is never caught by that check, and the column has no `not_null` test.
- The `not_null` tests on `orders.*_amount` pass because the `else 0` produces 0, not NULL.
- Nothing checks that the per-method amounts add up to `amount`.

**A second thing to check: possible duplicate payments.** `payment_id` 114 is `(order 59, NULL, 28.0)`, which is identical to payment 68. The classic jaffle_shop feed has payment ids 1–113, and you have 118 rows. So ids 114+ look like re-delivered rows, perhaps from the same ingestion change. They pass `unique(payment_id)` because they got new ids. If they are duplicates, the March total of 708.00 (and order 59 at 56.00) is overstated too. Reconciling March `credit_card` against the processor's 334.00 after the fix will tell you.

## 2. Fix

First, look at the raw data to confirm the cutover and the value format:

```sql
select payment_method, method, count(*), min(id), max(id)
from raw_payments group by 1, 2 order by 4;
```

If `method` uses the same values as before (`credit_card`, `coupon`, ...), make this change in `models/staging/stg_payments.sql`:

```sql
renamed as (

    select
        id as payment_id,
        order_id,
        -- the feed renamed payment_method to method from March 2018; old rows only have payment_method
        coalesce(payment_method, method) as payment_method,

        -- `amount` is currently stored in cents, so we convert it to dollars
        amount / 100 as amount

    from source

)
```

If the values differ (for example `'Credit Card'` or `'CC'`), normalise them inside that `coalesce` with `lower`/`replace` or a mapping `case`. Once the ingestion team confirms `payment_method` is permanently retired, switch to `method` alone.

Then rebuild:

```
dbt build -s stg_payments+
```

`orders` is a table, so it is fully rebuilt. `daily_revenue` is unaffected because it doesn't use payment methods.

For the suspected duplicates, check before deduplicating:

```sql
select order_id, coalesce(payment_method, method) as pm, amount, list(id order by id) as ids, count(*)
from raw_payments group by all having count(*) > 1;
```

If ids 114–118 are true re-deliveries, fix them at ingestion. A stopgap is to dedupe in staging with `qualify row_number() over (partition by order_id, coalesce(payment_method, method), amount order by id) = 1`. Only do this if a legitimate repeat payment of the same amount can't happen, so confirm that with finance or the processor first.

## 3. Tests that would have caught it

Add a `not_null` test on the staging column in `models/staging/schema.yml`. This one alone would have failed the build on the first March load:

```yaml
  - name: stg_payments
    columns:
      - name: payment_id
        tests:
          - unique
          - not_null
      - name: payment_method
        tests:
          - not_null
          - accepted_values:
              arguments:
                values: ['credit_card', 'coupon', 'bank_transfer', 'gift_card']
```

Add a reconciliation test so the mix can never silently diverge from the total, whatever the cause. Create `tests/assert_order_payment_mix_sums_to_amount.sql`:

```sql
-- the per-method amounts must add up to the order total; a payment with an unknown or null method breaks this
select
    order_id,
    amount,
    credit_card_amount + coupon_amount + bank_transfer_amount + gift_card_amount as method_total
from {{ ref('orders') }}
where abs(coalesce(amount, 0) - (credit_card_amount + coupon_amount + bank_transfer_amount + gift_card_amount)) > 0.001
```

Optionally, add a uniqueness test on `(order_id, payment_method, amount)` in `stg_payments` set to `severity: warn`, for example with `dbt_utils.unique_combination_of_columns`. That would flag re-delivered payments like id 114 that the `payment_id` uniqueness test misses.
