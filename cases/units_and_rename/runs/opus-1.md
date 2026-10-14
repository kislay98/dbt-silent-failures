claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 58 s | USD 0.188 | 2026-10-14 | project f769fdf | prompt afa3f72d4d9f

# Root cause: a schema change in `raw_payments` that staging doesn't handle

The bug is in the data, not the dbt logic. Starting in March 2018, the rows in `raw_payments` changed in two ways. Neither change breaks a model or fails a test.

### 1a. Payment method moved to a new column, so every method bucket shows 0

`information_schema` shows a `raw_payments.method` column that nothing in the project reads. `models/staging/stg_payments.sql` only selects `payment_method`, which is `NULL` for March and April rows (payments 66, 67, 86, 105 and 106 in your sample). Downstream, `models/orders.sql` does this:

```sql
sum(case when payment_method = '{{ payment_method }}' then amount else 0 end)
```

A `NULL` method matches no branch, so all four buckets get `0`. The `sum(amount)` total still counts every payment. That is why each method shows 0.00 while the month total is non-zero.

The tests stayed green for these reasons:
- `accepted_values` on `stg_payments.payment_method` is effectively `where payment_method not in (...)`. `NULL not in (...)` evaluates to `NULL`, so null rows are never flagged.
- The `not_null` tests on `orders.*_amount` can't fail, because `else 0` turns "unknown method" into a real zero.

I'm inferring that the method values now sit in `method`, because the column appeared exactly when `payment_method` went null. Confirm it with the query below.

### 1b. Some new rows hold dollars, not cents, so the totals are short

`stg_payments` divides every amount by 100. The arithmetic shows that a few new rows were loaded in dollars:

| | Total in `orders` | Settlement | Shortfall | Rows shown as cents |
|---|---|---|---|---|
| March | 595.27 | 622.00 | 26.73 = 99 × **0.27** | orders 76, 81, 86: 0.02 + 0.02 + 0.23 = **0.27** |
| April | 106.33 | 139.00 | 32.67 = 99 × **0.33** | orders 92 (payment 106), 94, 95: 0.02 + 0.07 + 0.24 = **0.33** |

So raw values like `2`, `23` and `24` are really $2, $23 and $24, not cents. If those rows are scaled correctly, both months reconcile exactly to the settlement figures. The scaling is mixed even inside one order. Order 92 has payment 105 = 1500 cents (correct) and payment 106 = 2 (really $2.00). Nothing visible in the row tells you which unit it uses, other than "not a multiple of 100". In January and February every amount is a whole dollar.

Run this to confirm both issues before fixing anything:

```sql
select date_trunc('month', o.ordered_at) as month,
       count(*)                                         as payments,
       count(*) filter (where p.payment_method is null) as null_payment_method,
       count(*) filter (where p.method is not null)     as has_method,
       count(*) filter (where p.amount % 100 <> 0)      as not_whole_cents
from raw_payments p
join raw_orders o on o.id = p.order_id
group by 1 order by 1;
```

Also check `select distinct method from raw_payments` to make sure it uses the same four values.

## 2. Fix

**Method: code change in `models/staging/stg_payments.sql`**

```sql
renamed as (

    select
        id as payment_id,
        order_id,
        -- the loader started writing the method to `method` in March 2018
        coalesce(payment_method, method) as payment_method,

        -- `amount` is currently stored in cents, so we convert it to dollars
        amount / 100 as amount

    from source

)
```

**Amount: data fix at the source.** Don't guess the unit in the model. A raw `2` could legitimately be 2 cents someday, so the unit can't be inferred from the value. Have the ingestion team do two things:
- Correct the mis-scaled rows in `raw_payments`. These are the rows from March onward where `amount % 100 <> 0`: payment 86, payment 106, and the payments for orders 81, 86, 94 and 95. Multiply each by 100.
- Fix the loader so it writes cents consistently. Otherwise the next load will overwrite your correction.

If you need correct numbers before the loader is fixed, you could add `case when amount % 100 <> 0 then amount * 100 else amount end` in staging. That only works because this data has no sub-dollar payments, so treat it as temporary and remove it once the source is clean.

**Rebuild:** `daily_revenue` is incremental and only reprocesses the last 3 days, so its March and April rows will stay wrong after a normal run. Run this:

```
dbt build --full-refresh -s stg_payments+
```

**Expected result:** March totals 622.00 with coupons = 45.00, and April totals 139.00 with coupons = 33.00. These match the settlement and the coupon ledger.

## 3. Tests that would have caught this

`models/staging/schema.yml`: add `not_null` to the method. This catches 1a directly, because `accepted_values` ignores nulls.

```yaml
      - name: payment_method
        tests:
          - not_null
          - accepted_values:
              arguments:
                values: ['credit_card', 'coupon', 'bank_transfer', 'gift_card']
```

`tests/assert_order_payment_methods_sum_to_amount.sql`: the method buckets must add up to the total. This catches any method that falls through the `else 0`.

```sql
select order_id
from {{ ref('orders') }}
where abs(credit_card_amount + coupon_amount + bank_transfer_amount + gift_card_amount
          - coalesce(amount, 0)) > 0.001
```

`tests/assert_raw_payment_amounts_in_whole_cents_of_dollars.sql`: this catches 1b. It assumes all payments are whole dollars, which holds for this data. Relax it if that ever changes.

```sql
select id, order_id, amount
from {{ ref('raw_payments') }}
where amount % 100 <> 0
```

Two longer-term suggestions:
- Add a monthly reconciliation test against the processor settlement, if you can load it into the warehouse. That is the check that actually guards revenue.
- Put a model contract on `stg_payments`, or add schema-change alerting in the ingestion job, so a new or renamed column gets flagged instead of silently ignored.

One unrelated thing I noticed in the log: 29 tests were found but only 27 ran. The two seed tests on `raw_customer_regions` aren't being run in the nightly build, which suggests seeds are excluded. If that table is loaded by ingestion now, consider declaring it as a `source` so its tests run.
