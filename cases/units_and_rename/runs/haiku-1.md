claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 40 s | USD 0.006 | 2026-10-14 | project bef6b69 | prompt aaad08d98757

## 1. Root cause

**The raw payments feed changed on 2018-03-01, and `stg_payments` only reads the old column.**

- `raw_payments` now has two method columns, `payment_method` and `method`. Orders from February (56) have `payment_method` populated. From March on (57, 58, 76, 92), `payment_method` is NULL in `stg_payments`. The ingestion job appears to have moved the value to `method`, but this is inferred from the schema and the NULLs, not confirmed.
- `jaffle_shop/models/staging/stg_payments.sql` selects only `payment_method`, so the NULLs pass straight through.
- `jaffle_shop/models/orders.sql` computes each method column as `sum(case when payment_method = '...' then amount else 0 end)`. A NULL never equals a string, so every March and April order gets 0 for every method. `amount` (`sum(amount)`) doesn't depend on the method, so it still has the payment values. That is why order 58 shows `amount = 24.0` with all four method columns at 0.0.
- The coupon gap follows from the same bug. Coupon payments from March onward have NULL methods, so `coupon_amount` is 0 instead of the 45.00 in the ledger.

**Why every test passes:**

- `accepted_values` on `stg_payments.payment_method` ignores NULLs (it checks `where col not in (...)`, which NULL never satisfies), so it passes.
- There is no `not_null` on `stg_payments.payment_method`. The staging schema only tests `payment_id` and `status`.
- `not_null` on `orders.*_amount` can't fail, because `sum(case ... else 0 end)` never returns NULL. The test checks a column that the model guarantees is non-null, even when the value is wrong.
- Nothing checks that the per-method amounts add up to `amount`.

**Files involved:** `jaffle_shop/models/staging/stg_payments.sql` (where the bad value enters), `jaffle_shop/models/orders.sql` (where it becomes silent zeros), and `jaffle_shop/models/staging/schema.yml` and `jaffle_shop/models/schema.yml` (missing or vacuous tests). The ingestion job that writes `raw_payments` is the upstream source.

**The 26.73 (March) and 32.67 (April) gap in totals is not explained by this bug.** `amount` sums every payment for each order whatever its method, so the NULL method alone can't cause it. The likely causes are payments whose `order_id` isn't in `stg_orders` (`orders` is built from a left join off `stg_orders`, so those payments silently disappear), settlement-side refunds or fees, or payments dated outside the month. I haven't verified which one applies. To check, run:

```sql
-- payments with no matching order (dropped by orders.sql)
select p.payment_id, p.order_id, p.amount
from stg_payments p
left join stg_orders o on p.order_id = o.order_id
where o.order_id is null;

-- payments per month via their order date, to compare with settlement
select date_trunc('month', o.order_date) as month, sum(p.amount) as paid
from stg_payments p
join stg_orders o on p.order_id = o.order_id
group by 1 order by 1;
```

## 2. Fix

**Confirm the column mapping first:**

```sql
select payment_method is null as legacy_col_null, method, count(*) as n, min(order_id), max(order_id)
from raw_payments
join ... -- or simply:
from raw_payments
group by all
order by all;
```

Check that `method` holds the same values as `payment_method` (`credit_card`, `coupon`, `bank_transfer`, `gift_card`) and that the coupon total for March is 45.00. If `method` uses different casing or labels, normalize them as well.

**Code fix in `jaffle_shop/models/staging/stg_payments.sql`:**

```sql
renamed as (

    select
        id as payment_id,
        order_id,
        -- the feed moved the method from `payment_method` to `method` on 2018-03-01;
        -- fall back to the new column so both shapes map to the same value
        coalesce(payment_method, method) as payment_method,

        -- `amount` is currently stored in cents, so we convert it to dollars
        amount / 100 as amount

    from source

)
```

Also raise this with the ingestion owner. The raw table should have one canonical method column, and the job should be fixed so the pipeline doesn't depend on the staging fallback.

**Rebuild:**

```sql
dbt build --select stg_payments+
```

`daily_revenue` is incremental and rebuilds only the last three days, so if the order totals change for March or April, rebuild it with `dbt build --select daily_revenue --full-refresh`. The method fix alone shouldn't change `daily_revenue`, since it sums `amount`, but the orphan-payment question might.

## 3. Tests that would have caught this

**a. `not_null` on the staged method (add to `jaffle_shop/models/staging/schema.yml`):**

```yaml
  - name: stg_payments
    columns:
      - name: payment_method
        tests:
          - not_null
          - accepted_values:
              arguments:
                values: ['credit_card', 'coupon', 'bank_transfer', 'gift_card']
```

`accepted_values` ignores NULLs, so it needs the `not_null` alongside it.

**b. Singular test that the method columns add up to the order total (`tests/assert_order_method_amounts_add_up.sql`):**

```sql
-- per-method amounts must sum to the order total; a NULL payment_method silently makes them 0
select
    order_id,
    amount,
    credit_card_amount + coupon_amount + bank_transfer_amount + gift_card_amount as method_total
from {{ ref('orders') }}
where abs(coalesce(amount, 0) - (credit_card_amount + coupon_amount + bank_transfer_amount + gift_card_amount)) > 0.001
```

Before the fix, this returns order 57 (amount 2.0, methods 0) and order 58 (amount 24.0, methods 0), plus every other March and April order with payments. The model's `not_null` tests can't catch these, because the values are 0 rather than NULL.

**c. Payments must reconcile to orders (`tests/assert_payments_have_orders.sql`):**

```sql
-- payments whose order is missing from stg_orders are dropped silently by orders.sql
select p.payment_id, p.order_id, p.amount
from {{ ref('stg_payments') }} p
left join {{ ref('stg_orders') }} o on p.order_id = o.order_id
where o.order_id is null
```

This test is not a guaranteed catch for the 26.73 and 32.67 gaps. It only flags orphan payments, which is one of the candidate causes. If the orphan query returns nothing, the total gap comes from somewhere else (settlement fees or refunds, or date boundaries), and the next step is a monthly reconciliation against the settlement figures.
