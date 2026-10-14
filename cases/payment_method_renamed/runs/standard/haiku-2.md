claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 30 s | USD 0.006 | 2026-10-14 | project ad0b16b | prompt 57278f2875bf

## 1. Root cause

The bug is in the data, in the `payment_method` column of `raw_payments`, and it surfaces in `jaffle_shop/models/staging/stg_payments.sql`.

- The ingestion job now loads `raw_payments` with two method columns: `payment_method` and `method`. In the sampled rows, `payment_method` is NULL while `method` holds the real value (ids 93, 112, 111, 79, 97, 86). Where both are populated they agree (ids 28, 12, 57).
- `stg_payments` reads only `payment_method`, so every payment with a NULL `payment_method` gets no method label.
- `orders.sql` pivots on `payment_method = 'credit_card'` and so on. A NULL method matches none of the four `CASE` branches, so those payments count toward `amount` (because `total_amount = sum(payments.amount)`) but toward no method column. The `fees` join also misses them, so they get a fee rate of 0.
- Jan and Feb are complete: the four method columns sum exactly to `amount` (218+90+136+52 = 496; 295+17+74+29 = 415). March has `amount` = 622 and all four method columns = 0, so every March payment has a NULL `payment_method`. Month totals are right because `amount` never depends on the method.
- The tests can't catch this, for two reasons:
  - `accepted_values` on `stg_payments.payment_method` ignores NULLs, so it passes.
  - The `not_null` tests on `orders.*_amount` pass because the `CASE` returns `0`, not NULL.

The raw `payment_method` NULLs most likely come from the loader change that introduced `method`. I can't confirm which column the source treats as authoritative from what's here. Ask the ingestion owner, and check when `payment_method` first went NULL. A quick check is `select date_trunc('month', o.order_date) m, p.payment_method is null nulls, sum(p.amount/100) from stg_payments p join stg_orders o using (order_id) group by all`.

## 2. Fix

**Immediate, in staging** (`jaffle_shop/models/staging/stg_payments.sql`):

```sql
        id as payment_id,
        order_id,
        coalesce(payment_method, method) as payment_method,
```

Then rebuild downstream: `dbt build --select stg_payments+`. `orders` will be rebuilt with the correct March mix and fees. `daily_revenue` and `region_revenue` don't use the method, so their numbers won't change.

Before you ship this, confirm two things:
- `method` uses the same values as `payment_method` (the sample suggests it does; check distinct values and casing across all rows).
- Finance's 334.00 reconciles: `select sum(amount)/100 from raw_payments where method = 'credit_card' and date_trunc('month', ...) = March`. The March `credit_card_amount` should then be 334.00.

Expect the March `fees` to become non-zero. That is the correct behaviour, not a new bug.

**Proper fix, upstream:** have the ingestion owner populate `payment_method` (or rename `method` to it) and backfill the NULL March rows. Once that's done, drop the `coalesce` or keep it as a guard.

## 3. Test that would have caught it

Add a singular test that checks the payment mix reconciles to the order total. It fails for March today, which is exactly the symptom finance found:

`jaffle_shop/tests/assert_payment_mix_sums_to_amount.sql`:

```sql
select
    order_id,
    amount,
    credit_card_amount + coupon_amount + bank_transfer_amount + gift_card_amount as mix_total
from {{ ref('orders') }}
where abs(amount - (credit_card_amount + coupon_amount + bank_transfer_amount + gift_card_amount)) > 0.005
```

Also add a `not_null` test to `stg_payments.payment_method` in `jaffle_shop/models/staging/schema.yml`. That catches the NULLs at the source of the problem, and the `accepted_values` test stays as is.

## Other things I noticed (not the cause)

- `daily_revenue`, `region_revenue`, and `customers` are unaffected by the method columns. Their totals match the settlement, as you said.
- `jaffle_shop/models/schema.yml` documents a `total_order_amount` column on `customers` that the model doesn't produce. It's a docs-only mismatch, but it will confuse people reading the docs.
