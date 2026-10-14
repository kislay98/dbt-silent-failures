claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 42 s | USD 0.143 | 2026-10-14 | project ad0b16b | prompt e23be30fc7e7

## 1. Root cause

**`models/staging/stg_payments.sql`** divides every payment by 100:

```sql
amount / 100 as amount   -- "amount is currently stored in cents"
```

That is only true for credit_card, coupon and bank_transfer. In **`seeds/raw_payments.csv`** the 12 `gift_card` rows are already in whole dollars (ids 9, 22, 26, 48, 49, 67, 68, 71, 92, 99, 107, 110). For example, id 9 (order 9) is `23`, a $23.00 redemption, but staging turns it into 0.23.

The numbers reconcile exactly:
- The gift card rows sum to 205 raw, which is 2.05 after `/100`. Your 205.00 vs 2.05 is exactly the 100x error.
- The non-gift-card payments sum to 1467.00, plus 205.00 gives the processor's 1672.00. The current 1469.05 is 1467.00 + 2.05.

The error flows to `orders.gift_card_amount` and `orders.amount`, then to `customers.customer_lifetime_value`, `region_revenue.revenue` and `daily_revenue.revenue`. Fees are unaffected because the gift card fee rate is 0.

All 44 checks pass because none of them test amounts. They only test nulls, uniqueness, accepted values and relationships.

**`daily_revenue` is also stale and inconsistent with `orders`.** Jan–Mar already show the correct totals (496 / 415 / 622, which include the full gift card amounts), but April shows 122.17 instead of 139.00. The 16.83 gap is order 96 (2018-04-06, gift card 17), which falls inside the incremental model's 3-day rebuild window (`max(order_date) - 3 days`) and was recomputed with the buggy 0.17. Older rows were never recomputed.

I'm inferring that the earlier rows were written before the `/100` bug existed, since only the window rows show the bug. The incremental window can't heal history after a logic change, so `daily_revenue` needs a full refresh.

## 2. Fix

In `stg_payments.sql`, scale by payment method. This is a minimal change that matches the existing style:

```sql
        -- `amount` is stored in cents, except for gift_card, which the feed sends in dollars
        case
            when payment_method = 'gift_card' then amount
            else amount / 100
        end as amount
```

Then rebuild, including a full refresh for the incremental model:

```
dbt build --full-refresh
```

Expected after the fix:
- `sum(orders.amount)` is 1672.00.
- `sum(gift_card_amount)` is 205.00.
- Order 9 shows 23.00.
- `customers`, `region_revenue` and `daily_revenue` all total 1672.00, and April is 139.00.

I'd also confirm with the feed owner that gift card is always in dollars. A better long-term fix is to have the feed use one unit.

## 3. Tests that would have caught it

**a) Unit test on staging** (dbt ≥1.8), in `models/staging/schema.yml`:

```yaml
unit_tests:
  - name: stg_payments_units_by_method
    model: stg_payments
    given:
      - input: ref('raw_payments')
        rows:
          - {id: 1, order_id: 1, payment_method: credit_card, amount: 2300}
          - {id: 2, order_id: 2, payment_method: gift_card, amount: 23}
    expect:
      rows:
        - {payment_id: 1, order_id: 1, payment_method: credit_card, amount: 23.0}
        - {payment_id: 2, order_id: 2, payment_method: gift_card, amount: 23.0}
```

**b) Reconciliation against the provider statements.** Add `seeds/provider_statements.csv`:

```csv
payment_method,statement_total
gift_card,205.00
all,1672.00
```

Then add `tests/assert_orders_match_provider_statements.sql`:

```sql
with actual as (
    select 'gift_card' as payment_method, sum(gift_card_amount) as total from {{ ref('orders') }}
    union all
    select 'all', sum(amount) from {{ ref('orders') }}
)

select
    statements.payment_method,
    statements.statement_total,
    actual.total
from {{ ref('provider_statements') }} as statements
left join actual using (payment_method)
where abs(statements.statement_total - coalesce(actual.total, 0)) > 0.005
```

This one would have failed on the first build. Because the statement values are static in the seed, it needs updating each period. In practice, load them from the statement feed.

**c) Consistency test for the incremental model**, `tests/assert_daily_revenue_matches_orders.sql`. This would have flagged the stale April and history rows:

```sql
with expected as (
    select order_date, sum(amount) as revenue from {{ ref('orders') }} group by 1
)

select
    coalesce(expected.order_date, daily.order_date) as order_date,
    expected.revenue as expected_revenue,
    daily.revenue as actual_revenue
from expected
full join {{ ref('daily_revenue') }} as daily
    on expected.order_date = daily.order_date
where abs(coalesce(expected.revenue, 0) - coalesce(daily.revenue, 0)) > 0.005
```

I haven't run any of this, since the project files aren't in the working directory. The diagnosis comes from the logs and sample tables you pasted.
