claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 38 s | USD 0.138 | 2026-10-14 | project ad0b16b | prompt e23be30fc7e7

## 1. Root cause

**`models/staging/stg_payments.sql`** divides every payment by 100 ("amount is currently stored in cents"), but **`seeds/raw_payments.csv` is not in one unit**. Credit card, coupon and bank transfer rows are in cents. The 12 `gift_card` rows are already in dollars:

| payment ids | order_ids | raw amount |
|---|---|---|
| 9, 22, 26, 48, 49, 67, 68, 71, 92, 99, 107, 110 | 9, 19, 23, 43, 44, 58, 59, 62, 80, 87, 93, 96 | 23, 6, 23, 18, 11, 6, 28, 14, 3, 30, 26, 17 |

These sum to 205, which matches the provider statement. The `/100` turns them into 2.05 (order 9: payment id 9, 23 becomes 0.23).

The reconciliation also works out: 1469.05 − 2.05 = 1467.00 for the cents-based methods, and 1467.00 + 205.00 = 1672.00, finance's figure.

All 44 tests passed because none of them checks a value against anything external. `not_null` and `accepted_values` are satisfied by 0.23.

**Other affected models**
- `orders` (`gift_card_amount`, `amount`) is wrong for those 12 orders.
- `customers.customer_lifetime_value` is wrong.
- `region_revenue.revenue` sums to exactly 1469.05, so it is wrong too.
- `daily_revenue` is wrong in a different way: it is internally inconsistent with `orders`. Its monthly revenue totals 1655.17, which is neither 1469.05 nor 1672.00.
  - It is incremental and only rebuilds `order_date >= max - 3 days` (2018-04-06 onward).
  - Order 96 (04-06, gift card 17) is in that window and carries the buggy 0.17.
  - Order 93 (04-03, gift card 26) is outside the window and still carries a correct 26.00 from an earlier build.
  - 1672.00 − 17 + 0.17 = 1655.17, which is exactly the total.
  - I'm inferring that an earlier run had correct gift card amounts. I can't see that history, but the numbers fit exactly.

## 2. Fix

In `stg_payments.sql`, normalise the unit per method:

```sql
        -- `amount` is stored in cents, except gift_card rows, which the provider feed sends in dollars
        case
            when payment_method = 'gift_card' then amount
            else amount / 100
        end as amount
```

Then rebuild, with a full refresh because the incremental model won't repair its older rows:

```
dbt build --full-refresh -s stg_payments+
```

Better long-term: ask the feed owner to send gift cards in cents, then drop the `case`. If you'd rather patch the data, multiply the 12 gift card rows in `raw_payments.csv` by 100 instead. Don't do both, or gift cards will be 100x too large.

After the rebuild, `sum(orders.amount)` should be 1672.00, `sum(gift_card_amount)` should be 205.00, and order 9 should show 23.00.

## 3. Tests that would have caught it

**a) Reconcile to the external control totals.** Add a small seed, for example `seeds/provider_statements.csv`, with `payment_method,statement_total` and rows such as `gift_card,205.00`. Then add `tests/assert_orders_reconcile_to_statements.sql`:

```sql
with model_totals as (
    {% for pm in ['credit_card', 'coupon', 'bank_transfer', 'gift_card'] -%}
    select '{{ pm }}' as payment_method, sum({{ pm }}_amount) as model_total from {{ ref('orders') }}
    {% if not loop.last %}union all{% endif %}
    {% endfor %}
)

select s.payment_method, s.statement_total, m.model_total
from {{ ref('provider_statements') }} s
join model_totals m using (payment_method)
where abs(s.statement_total - m.model_total) > 0.005
```

You can add a row for the 1672.00 settlement total the same way. Without this test the pipeline can't know its own numbers are wrong.

**b) Mart consistency.** This catches the stale `daily_revenue` window. Add `tests/assert_daily_revenue_matches_orders.sql`:

```sql
select d.revenue, o.amount
from (select sum(revenue) as revenue from {{ ref('daily_revenue') }}) d
cross join (select sum(amount) as amount from {{ ref('orders') }}) o
where abs(d.revenue - o.amount) > 0.005
```

Test (b) fails on the current data (1655.17 vs 1469.05). After the fix and full refresh both tests pass, and (b) would have flagged the incremental drift independently.

I haven't run any of this, since the project files weren't available in the working directory. The analysis comes from the files and output you pasted.
