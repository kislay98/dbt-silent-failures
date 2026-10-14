claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 48 s | USD 0.167 | 2026-10-14 | project f769fdf | prompt afa3f72d4d9f

# Root cause

**Short version:** the March data in `raw_payments` was loaded differently from January and February. It has two problems. The first problem zeroes every payment-method column. The second problem makes the monthly totals too small. No test catches either one.

### 1. Payment method moved to a new column (this causes the 0.00 by method)

`raw_payments` now has an extra column called `method`. You can see it in the `information_schema` output. From March onward, the ingestion job writes the payment method into `method` and leaves `payment_method` as NULL. The `stg_payments` sample confirms this: every row for orders 58, 76 and 92 has `payment_method = NULL`.

`models/staging/stg_payments.sql` only reads `payment_method`. In `models/orders.sql`, each `sum(case when payment_method = '...' then amount else 0 end)` therefore falls through to `0` for these rows. Meanwhile `sum(amount)` still counts them. That gives you `amount = 595.27` with every method column at `0.0`.

### 2. Some rows are in dollars, not cents (this causes 595.27 vs 622.00)

`stg_payments.sql` divides every amount by 100. Some March and April rows were loaded in dollars, so after the division they show up as cents-sized values. Examples are payment 86 at 0.02 and payment 106 at 0.02, plus the amounts on orders 81, 86, 94 and 95.

The arithmetic matches exactly. If X dollars were loaded as X instead of 100·X, the shortfall is 0.99·X:

| Month | Settled | Reported | Shortfall | X = shortfall / 0.99 | Tiny rows shown in sample |
|---|---|---|---|---|---|
| March | 622.00 | 595.27 | 26.73 | **27.00** | 0.02 + 0.02 + 0.23 = **0.27** |
| April | 139.00 | 106.33 | 32.67 | **33.00** | 0.02 + 0.07 + 0.24 = **0.33** |

April's 33.00 equals the coupon ledger exactly. That strongly suggests the coupon feed started sending dollars. In March, 45.00 of coupons = 27.00 (in dollars) + 18.00. A plausible reading is that the switch happened partway through March, with payment 66 (18.0) being a coupon still sent in cents. Confirm this with the query below; I can't see the raw rows from here.

```sql
select date_trunc('month', o.ordered_at) as month, p.method, p.payment_method,
       count(*) as n, sum(p.amount) as raw_amount, min(p.amount) as min_raw
from raw_payments p join raw_orders o on o.id = p.order_id
where o.ordered_at >= '2018-02-15'
group by all order by 1, 2;
```

### Why every test passed

- `accepted_values` on `payment_method` ignores NULLs. The check `NULL not in (...)` does not evaluate to true, so NULL rows never count as failures.
- The `not_null_orders_*_amount` tests pass because of the `else 0`.
- Nothing checks that the per-method amounts add up to the total, or that amounts are in a sensible unit.

# Fix

**The real fix belongs in the data.** Have the ingestion job reload March and April with two corrections:
- the method written to `payment_method`
- the amounts in cents

If the coupon feed changed units, fix it at the loader. After that, `stg_payments` needs no change to its unit handling.

**Until that happens, make staging tolerant of both column names.** Only patch the units in SQL once the query above shows a reliable way to identify the dollar rows. For example, if it's "coupon rows loaded from date D onward", you could write `case when <that condition> then amount else amount / 100 end`. Guessing a cutoff by size, such as `amount < 100`, would silently corrupt genuine small payments.

```sql
-- models/staging/stg_payments.sql
    select
        id as payment_id,
        order_id,
        -- ingestion moved the method into `method` from March 2018
        coalesce(payment_method, method) as payment_method,

        -- `amount` is currently stored in cents, so we convert it to dollars
        amount / 100 as amount

    from source
```

**Then rebuild with a full refresh.** `daily_revenue` is incremental and only rebuilds the last three days, so March and April revenue would stay wrong without it:

```
dbt build --full-refresh --select stg_payments+
```

# Tests that would have caught it

Add these to `models/staging/schema.yml` and `models/schema.yml`.

**1. Make `payment_method` non-null.** This alone would have failed on the first March load.

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

**2. Check that the per-method amounts sum to the total.** This catches any method that falls through the `case`, whatever the cause. Save it as `tests/assert_orders_payment_methods_sum_to_amount.sql`:

```sql
select order_id
from {{ ref('orders') }}
where abs(credit_card_amount + coupon_amount + bank_transfer_amount + gift_card_amount
          - coalesce(amount, 0)) > 0.001
```

**3. Check the units.** Every payment should be a whole number of cents and at least one dollar. A data test can enforce this: `where amount < 100` against `raw_payments`, run at `severity: warn` if small payments are legitimate. The stronger option is a singular test that compares monthly totals to the processor settlement table, if you can load it into the warehouse. That check would have flagged 595.27 vs 622.00 directly.

It also helps to declare `raw_payments` as a source with the expected columns. Then a new or renamed column like `method` shows up in review instead of slipping through.

# A separate issue I noticed

`raw_customer_regions` has an `updated_at` column, and there is no `unique` test on `customer_id`. If the regions feed keeps history, a customer can appear more than once and `region_revenue` will double-count that customer's revenue. Consider adding a `unique` test on `customer_id`, or deduplicating to the latest row per customer.
