claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 45 s | USD 0.171 | 2026-10-14 | project f769fdf+wk6ext | prompt deccfeb9fbe9

## 1. Root cause

**`raw_payment_methods` has more than one row for `credit_card`, and `models/orders.sql` joins to it on `payment_method` alone.** That join duplicates every credit card payment before the `sum()`.

The table is a rate history, not a lookup. Its loaded columns include `effective_from DATE`, which the project never uses. The seed description in `models/schema.yml` ("One row per payment method") and the join in `orders.sql` both assume one row per method. The processor feed now has two `credit_card` rows, presumably because the fee rate changed. In the `order_payments` CTE:

```sql
from payments
left join payment_methods
    on payments.payment_method = payment_methods.payment_method   -- 2 matches for credit_card
group by payments.order_id
```

Each credit card payment matches two rows, so `credit_card_amount`, `total_amount` and `fees` are all summed twice. The numbers fit this exactly:

| Evidence | Value |
|---|---|
| `stg_payments` credit card total | 871.00 (correct) → `orders` 1742.00 (×2) |
| `orders.amount` total | 1672 + 871 = **2543** |
| Order 1 | one 10.00 credit card payment → 20.00 |
| Order 13 | credit card 5 → 10, bank transfer 14 unchanged |
| `fees` | order 1: 0.58 = 10 × 0.058, order 6: 0.35 ≈ 6 × 0.058, order 99: 1.39 ≈ 24 × 0.058. 0.058 is the **sum of the two credit card fee rates**, so fees are also overstated and finance hasn't noticed yet. |
| `region_revenue`, `customers` | correct, because they read `stg_payments` and never join `raw_payment_methods` |

**Why every test passed:**
- Uniqueness is only tested on `orders.order_id`, which the `group by` keeps unique.
- The `not_null` tests can't detect inflated values.
- The only tests on `raw_payment_methods` are `not_null`, and they didn't run. The log says "Found … 32 data tests" but only 28 ran, and no seeds appear in the 35 nodes. The nightly build excludes seeds because ingestion loads them, so their tests are skipped too.

(I don't have the seed data, so I can't tell whether the second row is a new rate with a later `effective_from` or an exact duplicate. The fix below handles both.)

## 2. Fix

Use the fee rate that was in effect on the order date, so each payment matches exactly one row. In `models/orders.sql`:

```sql
payment_methods as (

    {#-
    Processor and fee rate per payment method, loaded as a seed like the other raw tables.
    One row per payment method and effective_from date, valid until the next effective_from
    #}
    select
        payment_method,
        fee_rate,
        effective_from,
        lead(effective_from) over (partition by payment_method order by effective_from) as effective_to

    from {{ ref('raw_payment_methods') }}

),

order_payments as (

    select
        payments.order_id,

        {% for payment_method in payment_methods -%}
        sum(case when payments.payment_method = '{{ payment_method }}' then payments.amount else 0 end) as {{ payment_method }}_amount,
        {% endfor -%}

        sum(payments.amount) as total_amount,
        round(sum(payments.amount * coalesce(payment_methods.fee_rate, 0)), 2) as fees

    from payments

    left join orders
        on payments.order_id = orders.order_id

    left join payment_methods
        on  payments.payment_method = payment_methods.payment_method
        and orders.order_date >= payment_methods.effective_from
        and (orders.order_date < payment_methods.effective_to or payment_methods.effective_to is null)

    group by payments.order_id

),
```

Notes on the fix:
- **Exact duplicates:** if two rows share the same `effective_from`, the earlier row gets an empty date range, so the payment still matches only one row. Which of the two rates wins is then arbitrary, which is why the uniqueness test below matters.
- **Orders before the first `effective_from`:** these get no match and a fee of 0 through the `coalesce`. Check that the earliest `effective_from` in the feed covers January 2018.
- **Description:** update `raw_payment_methods` in `models/schema.yml` to say "one row per payment method and effective_from date".

After the fix, credit card should total 871.00, `amount` should total 1672.00, and order 1 should show 10.00. `daily_revenue` reads from `orders`, and its incremental run only rebuilds the last three days. Its older rows still hold doubled revenue, so run `dbt build -s daily_revenue --full-refresh` once.

## 3. Tests that would have caught it

**a) Reconcile `orders` against `stg_payments`.** This catches any fan-out regardless of cause. Add `tests/assert_orders_amount_matches_payments.sql`:

```sql
with payments as (

    select order_id, sum(amount) as amount
    from {{ ref('stg_payments') }}
    group by order_id

)

select
    coalesce(orders.order_id, payments.order_id) as order_id,
    orders.amount as orders_amount,
    payments.amount as payments_amount

from {{ ref('orders') }} as orders

full outer join payments
    on orders.order_id = payments.order_id

where abs(coalesce(orders.amount, 0) - coalesce(payments.amount, 0)) > 0.001
```

Against the current output this test returns all 55 orders with credit card payments (order 1: 20.00 vs 10.00).

**b) Enforce the grain of the rate table**, in `models/schema.yml`:

```yaml
  - name: raw_payment_methods
    description: One row per payment method and effective_from date from the processor feed
    data_tests:
      - unique:
          column_name: "payment_method || '|' || cast(effective_from as varchar)"
    columns:
      - name: effective_from
        tests:
          - not_null
```

This test only helps if it actually runs. The nightly build currently skips seed tests, so either:
- declare the ingested `raw_*` tables as **sources** with these tests (the better long-term fix, since they aren't really seeds), or
- run `dbt test -s resource_type:seed` in the nightly job.

**Similar risk in `raw_customer_regions`:** it has an `updated_at` column, which suggests it can also hold history. `region_revenue` joins to it on `customer_id` alone, so it would double count the same way once a customer changes region. Its totals are correct today, but add the same uniqueness test on `customer_id` (or on `customer_id` and `updated_at`) and filter it to the current row.
