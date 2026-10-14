claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 39 s | USD 0.110 | 2026-10-15 | project ad0b16b | prompt 59943c0b3f30

## 1. Root cause

**Primary cause: `models/staging/stg_payments.sql` reads only `payment_method`, but from March on the ingestion job loads the method into a different column.**

- `raw_payments` now has two columns, `payment_method` and `method`. For January and February rows, `payment_method` is filled and `method` mirrors it.
- For March and April rows, `payment_method` is NULL and the value is only in `method`. The samples show ids 112, 111, 90, 97 and 74.
- `stg_payments` selects only `payment_method`, so those payments get a NULL method.
- In `orders.sql`, a NULL method matches none of the `case when payment_method = '...'` branches, so all four method columns are 0.00.
- Those same NULL rows fail the join to `raw_payment_methods`, so `fees` are also 0 for March and April.
- `amount` is still summed, which is why the totals look plausible (595.27 and 106.33).
- Every test passes because:
  - `accepted_values` on `stg_payments.payment_method` ignores NULLs.
  - The `not_null` tests on the `orders` method columns pass, since the `case` sums yield 0, not NULL.
  - Nothing checks that the method amounts add up to the total, or that `payment_method` is non-null.

**Secondary cause: part of the March and April amounts look like they were loaded in whole dollars, not cents.** Staging divides everything by 100, so those rows are understated by 99%.

- March is short by 622.00 − 595.27 = 26.73 = 0.99 × 27.
- April is short by 139.00 − 106.33 = 32.67 = 0.99 × 33.
- That fits $27 of March payments and $33 of April payments arriving as dollars. For example, `stg_payments` row 86 has amount 0.02.
- The April figure of 33 matches Marketing's April coupon ledger exactly, so I suspect the coupon rows are the dollar-denominated ones. March has 45 of coupons, so I'd expect part of the March coupons to be in dollars and the rest in cents. This is an inference. I can't see the seed data, so confirm it with a query before changing anything.

**Why `daily_revenue` shows 622.00 and 139.00:** it is incremental and only rebuilds the last three days. March and April were computed earlier from correct amounts and never refreshed, so it matches settlement by accident. It is stale, not an independent confirmation, and nothing reconciles it with `orders`.

## 2. Fix

Confirm the unit problem first. This lists the March/April rows that are small or only in the new column, which should total $27 in March and $33 in April:

```sql
select method, date_trunc('month', o.ordered_at) m, count(*), sum(amount)
from raw_payments p join raw_orders o on o.id = p.order_id
where p.payment_method is null
group by 1, 2 order by 2, 1;
```

Then change `stg_payments.sql`:

```sql
renamed as (

    select
        id as payment_id,
        order_id,
        coalesce(payment_method, method) as payment_method,

        -- `amount` is stored in cents, so we convert it to dollars
        amount / 100 as amount

    from source

)
```

- **Dollar-denominated rows:** fix these at the source, by having the ingestion job reload them in cents, rather than guessing in staging. If the query confirms they are all `coupon` rows from the new feed, a staging `case when payment_method = 'coupon' and <new-feed row> then amount else amount / 100 end` would also work, but it needs a reliable marker for those rows.
- **Rebuild:** run `dbt build --full-refresh -s daily_revenue+ orders+` so `daily_revenue` picks up March and April. Check that March now shows 622.00 with coupons of 45.00, and April shows 139.00 with coupons of 33.00.

## 3. Tests that would have caught it

Add a `not_null` test on `stg_payments.payment_method`, since `accepted_values` skips NULLs. It would have failed on the first March load.

```yaml
- name: payment_method
  tests:
    - not_null
    - accepted_values:
        arguments:
          values: ['credit_card', 'coupon', 'bank_transfer', 'gift_card']
```

Add a singular test, `tests/orders_methods_sum_to_amount.sql`, which fails when the method columns don't add up to the total:

```sql
select order_id, amount,
       credit_card_amount + coupon_amount + bank_transfer_amount + gift_card_amount as method_total
from {{ ref('orders') }}
where abs(amount - (credit_card_amount + coupon_amount + bank_transfer_amount + gift_card_amount)) > 0.005
```

Add a second singular test, `tests/daily_revenue_matches_orders.sql`, which compares `daily_revenue` with `orders` by month. It would have caught both the stale incremental rows and the amount gap:

```sql
select o.m, o.rev, d.rev
from (select date_trunc('month', order_date) m, sum(amount) rev from {{ ref('orders') }} group by 1) o
join (select date_trunc('month', order_date) m, sum(revenue) rev from {{ ref('daily_revenue') }} group by 1) d using (m)
where abs(o.rev - d.rev) > 0.005
```

Finally, add a source or schema-drift guard such as `dbt_utils.equal_rowcount` or a contract on `raw_payments`, so an unexpected new column like `method` is flagged.
