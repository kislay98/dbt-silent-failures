claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 38 s | USD 0.151 | 2026-10-14 | project f769fdf+wk6ext | prompt 335d8f2d0911

# Root cause: a duplicate `bank_transfer` row in `raw_payment_methods`

**Note:** I'm working only from the files and output you pasted. I haven't seen the seed CSV, so run the confirming query below before changing anything.

## 1. Root cause

**The data:** `raw_payment_methods` (loaded from `seeds/raw_payment_methods.csv`) has **two rows for `bank_transfer`**. This could be two processors, or the same method sent twice. The schema description says "one row per payment method", but nothing enforces it.

**The code that turns it into wrong totals:** in `models/orders.sql`, the `order_payments` CTE joins `stg_payments` to `payment_methods` *before* aggregating:

```sql
from payments
left join payment_methods
    on payments.payment_method = payment_methods.payment_method
group by payments.order_id
```

Every bank-transfer payment matches two rows, so it is counted twice in `bank_transfer_amount` and `total_amount`.

**Why the numbers fit this explanation:**

| Evidence | Consistent with the duplicate row |
|---|---|
| Bank transfer 822 vs 411 | Exactly doubled, and only for this method |
| Order 5: 34.00 vs one 17.00 payment | Doubled |
| Order 25: amount 78 = 22 + 16 + 2×20 | Only the bank-transfer leg is doubled |
| Overall gap: 2083 − 1672 = 411 | Equals the bank-transfer total |
| Fees | They imply roughly 2.9% for credit card and two bank-transfer rates that sum to 1% (order 5: 17 × 0.01 = 0.17; order 25: 22 × 0.029 + 20 × 0.01 = 0.84). The fee is applied once per matching row. |
| `region_revenue` and `customers` are correct | Neither model joins `raw_payment_methods` |

**Why every test passed:**
- No test checks that `payment_method` is unique in the seed.
- No test reconciles `orders` against `stg_payments`.
- The seed tests didn't run at all. The log shows "32 data tests" found but only 28 executed: the 4 tests on seed columns were skipped because seeds aren't part of the nightly selection.
- `unique_orders_order_id` can't detect this, because the fan-out happens inside a `group by order_id`.

Confirm the cause with:

```sql
select payment_method, count(*), list(processor), list(fee_rate)
from raw_payment_methods group by 1 having count(*) > 1;
```

## 2. Fix

**Data fix.** Correct the feed so there is one row per method, then rebuild. Which row to keep (which processor and rate is correct for bank transfer) is a decision for finance or ingestion, not something to guess in SQL.

**Code fix.** Make `orders.sql` compute amounts from payments alone, so a problem with fee metadata can never change revenue. Then join fees onto a payments table that is already one row per payment:

```sql
order_payments as (

    select
        payments.order_id,

        {% for payment_method in payment_methods -%}
        sum(case when payments.payment_method = '{{ payment_method }}' then payments.amount else 0 end) as {{ payment_method }}_amount,
        {% endfor -%}

        sum(payments.amount) as total_amount

    from payments

    group by payments.order_id

),

order_fees as (

    select
        payments.order_id,
        round(sum(payments.amount * coalesce(payment_methods.fee_rate, 0)), 2) as fees

    from payments

    left join payment_methods
        on payments.payment_method = payment_methods.payment_method

    group by payments.order_id

),
```

In `final`, add `left join order_fees on orders.order_id = order_fees.order_id` and select `order_fees.fees`.

The fees are still wrong while the duplicate exists, which is why you also need the uniqueness test below to fail the build.

**Afterwards, run:**

```
dbt build --full-refresh -s orders+
```

The full refresh is required for `daily_revenue`. It is incremental and only rebuilds the last 3 days, so its older rows keep the inflated revenue until you fully refresh it.

## 3. Tests that would have caught it

Add these to `models/schema.yml`.

**Uniqueness on the lookup key:**

```yaml
  - name: raw_payment_methods
    columns:
      - name: payment_method
        tests:
          - not_null
          - unique
```

This only helps if seed tests actually run in the nightly job. Either stop excluding them, or run `dbt test -s resource_type:seed` after ingestion. Better still, declare the raw tables as `sources` with these tests and run `dbt source freshness` / `dbt test -s source:*` as part of the job.

**A reconciliation test, attached to `orders` so it runs every time `orders` builds** (`tests/assert_orders_amount_matches_payments.sql`):

```sql
-- Order totals must reconcile with the payments they come from
select o.total, p.total
from (select sum(amount) as total from {{ ref('orders') }}) o
cross join (select sum(amount) as total from {{ ref('stg_payments') }}) p
where abs(o.total - p.total) > 0.005
```

This would have failed with 2083 vs 1672. It also catches any future fan-out, whatever its source.

## Related risk: `raw_customer_regions`

`raw_customer_regions` has an `updated_at` column. That suggests the feed can contain several rows per customer, which would fan out `region_revenue` in the same way. It isn't happening today, since that model reconciles to 1672, but adding a `unique` test on `customer_id` there would be worthwhile.
