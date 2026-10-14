claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 32 s | USD 0.005 | 2026-10-14 | project f769fdf+wk6ext | prompt b430ab6cd05c

## 1. Root cause

The bug is in `jaffle_shop/models/orders.sql`, and the bad data is in the `raw_payment_methods` table (seed or ingested copy). `orders.sql` joins payments to `raw_payment_methods` on `payment_method` alone:

```sql
from payments
left join payment_methods
    on payments.payment_method = payment_methods.payment_method
```

The join assumes `raw_payment_methods` has one row per `payment_method`, but its declared grain is "one row per payment method from the processor feed," and the feed carries a `processor` column. The numbers point to two `bank_transfer` rows, one per processor:

- Each bank transfer payment is joined twice, so `sum(payments.amount)` and `sum(case when ... 'bank_transfer' ...)` count it twice. That gives exactly 2x for bank transfer in every month.
- The fan-out explains the headline gap exactly: 2083.00 − 1672.00 = 411.00, which is the bank transfer total in `stg_payments`.
- Order 5 has one payment of 17.00 (`payment_id` 5), and the output shows 34.00.
- The `fees` column looks un-doubled (order 5 is 0.17 = 17 × 0.01), which suggests the duplicate row has `fee_rate` 0 or the rates are split across the two rows. I can't see the seed CSV, so I'm inferring this.

What rules out the other suspects:

- **Raw payments are fine.** `stg_payments` bank transfer totals 411.00, matching the statement, and `payment_id` is unique and tested.
- **Credit card, coupon and gift card are fine.** Each has one row per method, so their sums are unaffected.
- **`region_revenue` and `customers` are fine.** Both sum `stg_payments` directly and never touch the broken `orders` join, which is why they still total 1672.00.

Why the build passed: `orders.order_id` is still unique and not null, because the fan-out is collapsed by `group by payments.order_id` before the final select. No test reconciles `orders.amount` or the per-method columns back to `stg_payments`. `raw_payment_methods` has only a `not_null` test on `payment_method`, with no `unique`, even though its description promises one row per method.

To confirm, run this against the warehouse:

```sql
select payment_method, processor, fee_rate from raw_payment_methods order by 1;
```

I expect two `bank_transfer` rows.

## 2. Fix

**Data (primary fix).** Make `raw_payment_methods` one row per `payment_method`. Keep the processor and fee rate that apply to bank transfers, and remove the other row. If this table is loaded by the ingestion job rather than by `dbt seed`, make the fix there. Then reload it.

**Rebuild.** Because `daily_revenue` is incremental and only rebuilds the last three days, old days keep the inflated revenue. After the data fix, run:

```
dbt build --select raw_payment_methods+ --full-refresh
```

`--full-refresh` is needed for `daily_revenue`. The `+` picks up `orders`, `customers` and `daily_revenue` downstream of the seed. Use a plain `dbt seed` first if the seed is the source.

**Optional code guard.** Make `orders.sql` fail loudly on a bad feed rather than inflate silently. Once the `unique` test below exists, this is belt-and-braces and not strictly needed. I'd avoid a `max(fee_rate)` or `distinct` workaround, because it would silently pick one fee rate when the two rows disagree.

## 3. A test that would have caught this

Add the uniqueness test to `jaffle_shop/models/schema.yml` under the `raw_payment_methods` seed, matching the declared grain:

```yaml
  - name: raw_payment_methods
    columns:
      - name: payment_method
        tests:
          - unique
          - not_null
```

This fails at the seed, before `orders` is built.

Also add a reconciliation test that catches the symptom directly, regardless of cause. Create `jaffle_shop/tests/assert_orders_match_payments_by_method.sql`:

```sql
with paid as (

    select payment_method, sum(amount) as amount
    from {{ ref('stg_payments') }}
    group by 1

),

ordered as (

    select 'credit_card' as payment_method, sum(credit_card_amount) as amount from {{ ref('orders') }}
    union all
    select 'coupon', sum(coupon_amount) from {{ ref('orders') }}
    union all
    select 'bank_transfer', sum(bank_transfer_amount) from {{ ref('orders') }}
    union all
    select 'gift_card', sum(gift_card_amount) from {{ ref('orders') }}

)

select paid.payment_method, paid.amount as paid_amount, ordered.amount as ordered_amount
from paid
join ordered on paid.payment_method = ordered.payment_method
where abs(paid.amount - ordered.amount) > 0.005

union all

select 'total', (select sum(amount) from {{ ref('stg_payments') }}), (select sum(amount) from {{ ref('orders') }})
where abs((select sum(amount) from {{ ref('stg_payments') }}) - (select sum(amount) from {{ ref('orders') }})) > 0.005
```

This test passes on the current data only once the duplicate is removed. It would have failed on last night's build, pointing straight at `bank_transfer` (822 vs 411).

**Latent risk, not the cause of this incident.** `raw_customer_regions` has an `updated_at` column, so it may hold more than one row per customer over time. `region_revenue` would then fan out in the same way. Add `unique` on `customer_id` in that seed's tests, or dedupe to the latest `updated_at` before joining. The current 1672.00 total suggests this isn't happening yet.
