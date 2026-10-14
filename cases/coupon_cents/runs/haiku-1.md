claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 35 s | USD 0.005 | 2026-10-14 | project bef6b69

## 1. Root cause

The bug is a unit mismatch in some rows of `raw_payments`, not in the models' logic. The `amount` column is documented as cents, and `stg_payments.sql` divides every row by 100. Some coupon payments were loaded in dollars, so the division shrinks them by 100x.

The numbers confirm this. The affected rows are coupon payments on orders 76, 81, 86, 92, 94, and 95:

| Order | Stored amount (as read) | Stored value as loaded (if dollars) | Output after `/100` |
|---|---|---|---|
| 76 (Mar) | 0.02 | 2 | 0.02 |
| 81 (Mar) | 0.02 | 2 | 0.02 |
| 86 (Mar) | 0.23 | 23 | 0.23 |
| 92 (Apr) | 0.02 | 2 | 0.02 |
| 94 (Apr) | 0.07 | 7 | 0.07 |
| 95 (Apr) | 0.24 | 24 | 0.24 |

- **March:** dollars give 18.00 (order 58) + 2 + 2 + 23 = **45.00**, which matches the ledger. Output shows 18.27.
- **April:** dollars give 2 + 7 + 24 = **33.00**, which matches the ledger. Output shows 0.33.
- **Settlement:** the March gap is 622.00 − 595.27 = 26.73, which equals 27.00 − 0.27. The only discrepancy is these coupon rows.
- **Jan/Feb** have no affected rows, which fits a change in the ingestion format starting in March.

The affected rows also flow into `orders.amount` and `daily_revenue.revenue` (order 92 shows 15.02 where it should be 17.00), so revenue is understated for those days too.

The files responsible are:
- `jaffle_shop/models/staging/stg_payments.sql`: `amount / 100` is applied unconditionally. This is the model that turns the bad input into bad output, but it follows its documented contract, so the fix belongs upstream.
- The ingestion job that writes `raw_payments`, which is outside this repo. It is the source of the inconsistent units.

**Why every test passes:** the tests check shape (unique, not_null, accepted_values on the method names). A coupon payment of 2 dollars is still a valid integer, unique, not null, and tagged `coupon`. Nothing in the project compares values to an independent total. Also, there is no `relationships` test from `stg_payments.order_id` to `stg_orders`, though that would not have caught this particular bug.

I inferred this from the output numbers and did not have the seed or raw data. Confirm with:

```sql
select id, order_id, payment_method, amount
from raw_payments
where payment_method = 'coupon'
  and order_id in (76, 81, 86, 92, 94, 95);
```

You should see amounts of 2, 2, 23, 2, 7, 24. Then check the ingestion job's history for a change in March.

## 2. Fix

**a. Correct the source data.** Fix the ingestion so it writes cents consistently, and then correct the affected rows. Confirm the row ids from the query above before running this:

```sql
update raw_payments
set amount = amount * 100
where payment_method = 'coupon'
  and order_id in (76, 81, 86, 92, 94, 95);  -- verify ids first
```

**b. Rebuild, including the incremental model.** `daily_revenue` only rebuilds the last three days, so March and April history will stay wrong unless you full-refresh it:

```bash
dbt build --select stg_payments+ --full-refresh
```

Use `--full-refresh` on `daily_revenue` specifically if you want to limit the scope.

**c. Do not paper over this in `stg_payments.sql`** with a per-order override or a guess like "if amount < 1, multiply by 100". That would hide the upstream problem and could corrupt legitimate sub-dollar payments.

After the fix, `coupon_amount` should show 45.00 for March and 33.00 for April, and `sum(orders.amount)` for March should be 622.00.

## 3. A test that would have caught this

No generic schema test can detect a unit error from the data alone, because 2 dollars and 2 cents are both valid integers. The reliable check is a reconciliation against an independent total, such as the ledger or the processor settlement. I'll assume the ledger is loaded as a seed named `raw_coupon_ledger` with `redeemed_at` and `amount` columns. Adjust the names to match your data.

`jaffle_shop/tests/assert_coupon_revenue_matches_ledger.sql`:

```sql
-- Fails if modelled monthly coupon revenue drifts from the redemption ledger
with ledger as (

    select
        date_trunc('month', redeemed_at) as month,
        sum(amount) as ledger_amount

    from {{ ref('raw_coupon_ledger') }}
    group by 1

),

modelled as (

    select
        date_trunc('month', order_date) as month,
        sum(coupon_amount) as modelled_amount

    from {{ ref('orders') }}
    group by 1

)

select
    ledger.month,
    ledger.ledger_amount,
    modelled.modelled_amount

from ledger
join modelled using (month)
where abs(ledger.ledger_amount - modelled.modelled_amount) > 0.01
```

Caveats:
- The ledger may be keyed on redemption date and `orders` on order date. If they differ, reconcile on the matching key instead.
- A tolerance of 0.01 is a starting point. Use whatever rounding your processor applies.
- A matching check against the processor settlement, which here is the total that also disagreed by 26.73, would have caught this just as well.
