claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 46 s | USD 0.117 | 2026-10-15 | project ad0b16b | prompt 59943c0b3f30

## 1. Root cause

**`raw_payments` (ingestion output) changed shape, and `models/staging/stg_payments.sql` still reads only the old column.**

- From March orders onward, the ingestion job writes the payment method to a new column, `method`, and leaves `payment_method` NULL. The loaded table has both columns. January and February rows still have `payment_method` populated, and the March/April sample rows have it NULL.
- `stg_payments` selects only `payment_method`, so every March/April payment has `payment_method = NULL`.
- In `orders.sql`, every `case when payments.payment_method = '...'` is false, so each method column is 0.00. `amount` is `sum(payments.amount)`, which doesn't depend on the method, so the order total still comes through (595.27).
- The join to `raw_payment_methods` also fails on NULL, so `coalesce(fee_rate, 0)` gives `fees = 0.00` for those orders.
- Nothing flagged it because `accepted_values` ignores NULLs, there is no `not_null` on `stg_payments.payment_method`, and the `not_null` tests on `orders.*_amount` pass on 0.0.

**Second problem, hidden in the same rows:** the totals are also short, 595.27 against 622.00 settled and 106.33 against 139.00.
- The shortfalls are 26.73 = 27 × 0.99 and 32.67 = 33 × 0.99. The fractional parts of the totals (.27 and .33) match.
- That is the signature of integer-dollar amounts divided by 100 in `stg_payments`. For example, the sample row `payment_id 86` shows 0.02, which is raw `2`, not `200`.
- April's 33 equals marketing's April coupon total exactly. I couldn't query the data, so I'm inferring that the new feed loads coupon amounts in whole dollars instead of cents. March's ledger is 45, so only 27 of it appears to be mis-scaled. That is consistent with the unit switch happening mid-month, but it is not proven.
- Confirm with: `select method, min(amount), sum(amount) from raw_payments where payment_method is null and amount < 100 group by 1`.

**Why `daily_revenue` looks right:** it is incremental and only rebuilds the last 3 days. Its March/April history was computed before the feed changed, so it still holds the correct 622.00 and 139.00. A `--full-refresh` today would make it wrong too.

## 2. Fix

In `stg_payments.sql`, read the method from either column:

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

For the units, the right fix is for ingestion to send cents again and for March/April to be reloaded. If a stopgap is needed, scale only the affected rows, with the cutoff confirmed by the query above:

```sql
case
    when payment_method is null and method = 'coupon' and amount < 100 then amount  -- loaded in dollars
    else amount / 100
end as amount
```

Don't infer units from magnitude alone without checking. After that:

1. Run `dbt build --full-refresh -s daily_revenue+ orders+` so the incremental table is rebuilt from the corrected data.
2. Verify March and April method sums against 622.00 and 139.00, and coupons against 45 and 33.

The 3-day lookback in `daily_revenue` will also miss late-landing or restated payments. Widen it, or switch to a lookback based on the load timestamp.

## 3. Tests that would have caught it

Add to `models/staging/schema.yml`:

```yaml
      - name: payment_method
        tests:
          - not_null
          - accepted_values: ...   # existing
```

Add `tests/assert_order_methods_sum_to_amount.sql`, so an unclassified payment can't hide in the total:

```sql
select order_id, amount,
       credit_card_amount + coupon_amount + bank_transfer_amount + gift_card_amount as by_method
from {{ ref('orders') }}
where abs(amount - by_method) > 0.005
```

Add `tests/assert_daily_revenue_matches_orders.sql`, which catches stale or drifted incremental history:

```sql
select d.order_date, d.revenue, o.revenue as source_revenue
from {{ ref('daily_revenue') }} d
join (select order_date, sum(amount) as revenue from {{ ref('orders') }} group by 1) o
  using (order_date)
where abs(d.revenue - o.revenue) > 0.005
```

The first test fails for every March/April order with a total but no method amounts. The `not_null` test fails on the NULL `payment_method` rows. The second test would have flagged the 26.73 and 32.67 drift once the unit problem hit, as soon as `daily_revenue` was rebuilt in full.
