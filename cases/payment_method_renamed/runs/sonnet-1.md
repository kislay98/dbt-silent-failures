claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 17 s | USD 0.057 | 2026-10-14 | project bef6b69

I couldn't see the seed data, so this is inferred from the schema, log and output. It's consistent with every symptom, and a query below confirms it.

## 1. Root cause

`raw_payments` has two payment-method columns, `payment_method` and `method`. The ingestion job added `method` (schema drift) and appears to have written the method there for newer rows, leaving `payment_method` NULL. March and April payments are probably affected, since both months show zeros. January and February rows still have `payment_method` populated.

`models/staging/stg_payments.sql` only reads `payment_method`, so the affected rows end up with a NULL method. In `models/orders.sql`, every `case when payment_method = '...'` then evaluates false or NULL, giving 0.00 for each method. `sum(amount) as total_amount` doesn't depend on the method, so the order totals (622.00 for March) and `daily_revenue` still match the settlement.

Every test passed because none of them can see this:
- `accepted_values` on `stg_payments.payment_method` ignores NULLs.
- There is no `not_null` on `payment_method`.
- The `not_null` tests on `orders.*_amount` pass because the `else 0` branch never returns NULL.
- Nothing reconciles the per-method columns against `amount`.

Confirm with this query. It should return March and April rows with a non-null `method`:

```sql
select date_trunc('month', o.order_date) m, p.payment_method is null as pm_null, p.method, count(*), sum(p.amount)/100
from raw_payments p join raw_orders o on o.id = p.order_id
group by all order by 1;
```

## 2. Fix

In `models/staging/stg_payments.sql`:

```sql
    select
        id as payment_id,
        order_id,
        coalesce(payment_method, method) as payment_method,

        -- `amount` is currently stored in cents, so we convert it to dollars
        amount / 100 as amount

    from source
```

`orders`, `customers` and `region_revenue` are tables and pick this up on the next `dbt build`. `daily_revenue` doesn't use the method, so it is unaffected.

Check two things before shipping:
- If `method` uses different labels (e.g. `card`), map them to the four accepted values in the same `coalesce`. The existing `accepted_values` test will fail loudly if you don't, because the values will be non-null.
- Check whether both columns are ever populated with different values.

After the rebuild, March `credit_card` should be 334.00, which matches the processor statement. The four method columns should sum to `amount` in every month.

Also raise the schema change with the ingestion owners, so it's one column and not two.

## 3. Tests

Add `not_null` to the method column, since `accepted_values` skips NULLs:

```yaml
# models/staging/schema.yml
      - name: payment_method
        tests:
          - not_null
          - accepted_values: ...
```

Add a singular test, `tests/assert_orders_payment_methods_sum_to_amount.sql`, that reconciles the breakdown against the total:

```sql
select order_id, amount,
       credit_card_amount + coupon_amount + bank_transfer_amount + gift_card_amount as method_sum
from {{ ref('orders') }}
where amount is not null
  and abs(amount - (credit_card_amount + coupon_amount + bank_transfer_amount + gift_card_amount)) > 0.005
```

Either test would have failed on the first March load. Optionally, add a `not_null` or "column must be empty" check on `raw_payments.method` as a source freshness-style guard against future drift.
