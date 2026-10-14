claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 49 s | USD 0.224 | 2026-10-15 | project ad0b16b | prompt 59943c0b3f30

# Diagnosis: March/April payment mix in `orders`

## 1. Root cause

There are two problems, both introduced by the ingestion job's `raw_payments` feed for March and April 2018. No dbt file changed. The model that is now wrong is `models/staging/stg_payments.sql`.

### A. The payment method moved to a new column

`raw_payments` now has a second column, `method`. For March and April rows, the old `payment_method` column is NULL and the value is only in `method`. Sample rows:

| id | order_id | payment_method | amount | method |
|---|---|---|---|---|
| 112 | 98 | NULL | 1000 | bank_transfer |
| 90 | 79 | NULL | 1800 | credit_card |
| 28 | 25 | bank_transfer | 2000 | bank_transfer |

`stg_payments.sql` still selects `payment_method`, so in `orders.sql`:

- Every `case when payments.payment_method = '...'` returns 0, so all four method columns are 0.00.
- The left join to `raw_payment_methods` finds no match, so `coalesce(fee_rate, 0)` makes fees 0.00.
- `sum(payments.amount)` ignores the method, so `amount` is still filled in. This is why the month total exists but the breakdown is zero.

No test caught it because `accepted_values` on `stg_payments.payment_method` ignores NULLs and there is no `not_null` test on that column.

### B. Some coupon amounts arrived in dollars instead of cents

`stg_payments` divides every amount by 100. The shortfalls against settlement match exactly with coupon amounts that were loaded in dollars:

| Month | Settled | `orders` total | Shortfall | Coupon amount loaded in dollars |
|---|---|---|---|---|
| March | 622.00 | 595.27 | 26.73 | 26.73 = 27 × 0.99 → 27.00 of the 45.00 coupons |
| April | 139.00 | 106.33 | 32.67 | 32.67 = 33 × 0.99 → all 33.00 of the coupons |

The cents part of the totals (.27 and .33) is that dollar amount divided by 100. `customers` agrees: 1672.00 − 1612.60 = 59.40 = 26.73 + 32.67. Payment 86 (order 76, raw amount `2`, so 0.02) is an example.

### Why `daily_revenue` looks right

It shows 622 and 139 only because it is incremental. It rebuilds just the last three days, so its March and early April rows were built before the bad data arrived. A `--full-refresh` would make it wrong too. Nothing compares it with `orders`, so the disagreement went unnoticed.

## 2. Fix

### Code: `models/staging/stg_payments.sql`

```sql
renamed as (

    select
        id as payment_id,
        order_id,
        -- the feed moved payment_method to `method` from March 2018; older rows only have payment_method
        coalesce(payment_method, method) as payment_method,

        -- `amount` is currently stored in cents, so we convert it to dollars
        amount / 100 as amount

    from source

)
```

This restores the by-method columns and the fees. `orders.sql` does not need to change.

### Data: the coupon units

This has to be fixed at the source. The ingestion job should reload the affected coupon rows in cents, or patch them by multiplying by 100. No column marks which rows are affected: in March only 27.00 of the 45.00 coupons are wrong, and every March row has a NULL `payment_method`. A guess in SQL could scale the wrong rows, so I'd find the rows explicitly and confirm them against marketing's ledger:

```sql
select p.id, p.order_id, o.ordered_at, p.amount, p.amount / 100.0 as dollars_as_loaded
from raw_payments p join raw_orders o on o.id = p.order_id
where coalesce(p.payment_method, p.method) = 'coupon'
  and o.ordered_at >= '2018-03-01'
order by o.ordered_at;
```

After the fix, these should hold:

- March: coupon = 45.00, total = 622.00.
- April: coupon = 33.00, total = 139.00.

Then run `dbt build --full-refresh -s daily_revenue+` so the incremental table is rebuilt from corrected data.

## 3. Tests that would have caught this

### `models/staging/schema.yml`

Add `not_null` on `payment_method`. It catches problem A on the first bad load.

```yaml
      - name: payment_method
        tests:
          - not_null
          - accepted_values:
              arguments:
                values: ['credit_card', 'coupon', 'bank_transfer', 'gift_card']
```

### `tests/orders_payment_methods_sum_to_amount.sql`

The method columns must add up to the total. This catches problem A in `orders` even if staging is bypassed.

```sql
select order_id, amount,
       credit_card_amount + coupon_amount + bank_transfer_amount + gift_card_amount as by_method
from {{ ref('orders') }}
where abs(coalesce(amount, 0)
          - (credit_card_amount + coupon_amount + bank_transfer_amount + gift_card_amount)) > 0.005
```

### `tests/orders_reconcile_daily_revenue.sql`

This catches problem B. A unit change shows up as a disagreement with the incremental table, which still holds the earlier values.

```sql
with o as (
    select date_trunc('month', order_date) as month, sum(coalesce(amount, 0)) as amount
    from {{ ref('orders') }} group by 1
), d as (
    select date_trunc('month', order_date) as month, sum(coalesce(revenue, 0)) as revenue
    from {{ ref('daily_revenue') }} group by 1
)
select * from o full join d using (month)
where abs(coalesce(o.amount, 0) - coalesce(d.revenue, 0)) > 0.005
```

If the processor settlement or the coupon ledger is loaded into the warehouse, reconciling against it directly is a stronger check.

### Optional unit check on `raw_payments`

Every non-zero amount in the historical feed is a whole number of dollars. A `severity: warn` test on `amount % 100 <> 0` would flag rows like payment 86 straight away.
