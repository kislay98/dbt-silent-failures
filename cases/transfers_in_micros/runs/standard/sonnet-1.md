claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 28 s | USD 0.093 | 2026-10-15 | project ad0b16b | prompt 75e42f540dd8

## 1. Root cause

**The bank transfer rows in `raw_payments` (the seed or ingestion load) changed units partway through March.** From roughly mid-March the `amount` for `bank_transfer` payments is no longer in cents. It is 10,000× too large, which is consistent with micro-dollars (3.00 stored as 3,000,000). Credit card, coupon and gift card rows are still in cents.

- **Order 73:** the customer paid 3.00, so the correct raw value is 300 cents. The load has 3,000,000, and `stg_payments` divides by 100, giving 30,000.00.
- **April:** 390,000.00 / 39.00 is exactly 10,000×, so every April bank transfer is affected.
- **March:** 1,140,048 against 162 is not exactly 10,000×, because the first half of March is still correct. Only the later rows are inflated.
- **Samples:** `raw_payments` ids 105 and 111 hold 15,000,000 and 14,000,000, while earlier bank transfers hold values like 200, 400 and 2000.

`models/staging/stg_payments.sql` hard-codes the assumption "`amount` is in cents" (`amount / 100`) for every method. The dbt code did what it was told. The data broke the assumption.

**Why the tests passed:** none of them look at magnitude. They only check not_null, unique, accepted_values and relationships. Nothing compares totals to the processor statement or bounds `amount`.

**Knock-on damage:**
- `orders`, `customers` (lifetime value of 1.5M, for example) and `region_revenue` are all wrong.
- `daily_revenue` is wrong in a different way.
  - It is incremental and only rebuilds the last 3 days relative to `max(order_date)`.
  - Early and mid March keep their old correct values (622), while April is a mix of stale and bad rows (240,115, versus a true total of about 139).
  - Fixing the source and running normally will not repair the older bad April rows.

I haven't run anything. The diagnosis comes from the log and samples, and I can't see the seed data, so the exact cutoff row is unconfirmed.

## 2. Fix

**a) Fix the source.** Find the affected rows:

```sql
select id, order_id, amount
from raw_payments
where payment_method = 'bank_transfer' and amount >= 1000000
order by id;
```

Confirm the first bad `id` with whoever owns the ingestion job, and have them reload those rows in cents (`amount / 10000`). The feed format change also needs fixing upstream, or it will recur.

**b) Stopgap in `stg_payments.sql`** if the reload will take time. Replace `<first_bad_id>` with the boundary confirmed above:

```sql
-- bank_transfer feed switched to micro-dollars from payment <first_bad_id>; remove once raw_payments is reloaded
case
    when payment_method = 'bank_transfer' and id >= <first_bad_id> then amount / 1000000.0
    else amount / 100.0
end as amount
```

**c) Rebuild everything, including the incremental model:**

```
dbt build --full-refresh -s stg_payments+
```

A normal run would leave daily_revenue wrong. The expected result is bank transfers of 162.00 in March and 39.00 in April.

## 3. Test that would have caught it

Add a singular test, `tests/assert_payment_amounts_plausible.sql`. It fails if any payment is implausibly large, or if a method's monthly average jumps by more than 100× against the typical level:

```sql
-- no single payment above 1,000 (AUD); real payments top out in the low hundreds
select payment_id, payment_method, amount
from {{ ref('stg_payments') }}
where amount > 1000
```

Also add a reconciliation test against the processor statement. Load the statement as a seed, for example `statement_totals(month, payment_method, amount)`:

```sql
select s.month, s.payment_method, s.amount as statement_amount, p.amount as model_amount
from {{ ref('statement_totals') }} s
join (
    select date_trunc('month', o.order_date) as month,
           p.payment_method,
           sum(p.amount) as amount
    from {{ ref('stg_payments') }} p
    join {{ ref('stg_orders') }} o using (order_id)
    group by 1, 2
) p using (month, payment_method)
where abs(s.amount - p.amount) > 0.01
```

The first test would have failed on the first bad load. The second would have caught any subtler unit or scale drift.
