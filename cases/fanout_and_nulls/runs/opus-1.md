claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 47 s | USD 0.173 | 2026-10-14 | project f769fdf+wk6ext | prompt c0748c4eed7e

# Diagnosis: two independent data defects

The numbers split cleanly into two separate problems:

| | orders column | `stg_payments` | Processor |
|---|---|---|---|
| credit_card | 1706 | 853 | 871 |
| bank_transfer | 386 | 386 | 411 (−25) |
| coupon | 169 | 169 | 185 (−16) |
| gift_card | 199 | 199 | 205 (−6) |
| **total** | **2460** | **1607** | **1672** |

- `orders` minus staging is +853. That is exactly the credit card total counted a second time, so it is a fan-out that only `orders.sql` has.
- Staging minus the processor is −65 across all four methods. That is missing data, which is why `region_revenue` and `customers` also come to 1607.

## 1. Root cause

**A. `raw_payment_methods` has two `credit_card` rows. This is what inflates `orders`.**

`orders.sql` left-joins `payments` to `payment_methods` on `payment_method` and then sums. Each credit card payment matches twice, so it is counted twice:

- Order 2 has one 20.00 card payment and shows 40.00.
- Order 13 has a 5.00 card payment and shows 10.00.

The fees are inflated as well. For order 2, 1.16 / 20 = 0.058, which is the two rows' `fee_rate`s added together. The table carries a `processor` column, so this is probably a second card processor, or an old and a new processor, in the feed.

Two things let this through:
- No test checks that `payment_method` is unique in that table.
- The `not_null` tests on the seeds never ran. The log says "Found 32 data tests", but only 28 ran, because the seeds and their tests weren't part of the build selection.

**B. Eight `raw_payments` rows have `amount = NULL`, totalling 65.00. This is the shortfall everywhere.**

The `stg_payments` sample shows the NULL rows: 3 credit card, 3 bank transfer, 1 coupon and 1 gift card. That lines up with the per-method shortfalls:
- Credit card: 871 − 853 = 18
- Bank transfer, coupon and gift card: 25, 16 and 6

Order 13 settled for 19.00. That is 5.00 by card plus a 14.00 bank transfer (payment 15), and the 14.00 arrived as NULL.

`sum()` and `sum(case … then amount else 0 end)` both skip NULLs silently. Nothing tests `stg_payments.amount`, so the build passed.

The column is loaded as `INTEGER` cents. These values most likely failed the loader's cast (for example a decimal or formatted string) and were nulled. That is an ingestion-job problem, not a dbt one.

## 2. Fix

**Data, which must be fixed at the source:**
- **`raw_payments`:** re-ingest the 8 rows with their real amounts in cents, and fix the loader's cast so it errors instead of nulling. The values have to come from the source or the processor statement (payment 15 is 1400). Don't `coalesce(amount, 0)` in staging: that is effectively what happens today.
- **`raw_payment_methods`:** reduce it to one row per `payment_method`, keeping the correct processor and fee rate for credit card. Finance or whoever owns the feed should confirm which rate is correct. If two card processors are genuinely in use, `raw_payments` needs a processor column so the join can use it.

**Code:** make `orders.sql` immune to fan-out, so a bad lookup table can only affect `fees` and never the amounts:

```sql
payment_methods as (

    select * from {{ ref('raw_payment_methods') }}

),

order_payments as (

    select
        payments.order_id,

        {% for payment_method in payment_methods -%}
        sum(case when payments.payment_method = '{{ payment_method }}' then payments.amount else 0 end) as {{ payment_method }}_amount,
        {% endfor -%}

        sum(payments.amount) as total_amount,
        round(sum(payments.amount * coalesce(
            (select fee_rate from payment_methods where payment_methods.payment_method = payments.payment_method),
            0)), 2) as fees

    from payments

    group by payments.order_id

),
```

DuckDB raises an error if that scalar subquery returns more than one row, so a duplicate fails loudly instead of doubling revenue.

After the data is fixed, run:

```
dbt build --full-refresh -s daily_revenue
```

`daily_revenue` is incremental and only rebuilds the last 3 days, so its older days still hold the doubled credit card figures.

## 3. Tests that would have caught it

In `models/staging/schema.yml`, under `stg_payments`:

```yaml
      - name: amount
        tests:
          - not_null
```

This would have failed on the 8 rows.

In `models/schema.yml`, under `raw_payment_methods.payment_method`:

```yaml
        tests:
          - not_null
          - unique
```

This would have failed on `credit_card`. Also make sure seed tests run in the nightly job. Either include the seeds in the selection or declare these tables as sources with tests.

Add a reconciliation test, `tests/assert_orders_amount_matches_payments.sql`. It catches any fan-out or dropped payments, whatever the cause:

```sql
select *
from (select round(sum(amount), 2) as orders_total from {{ ref('orders') }}) o
cross join (select round(sum(amount), 2) as payments_total from {{ ref('stg_payments') }}) p
where o.orders_total <> p.payments_total
```

On the current data this returns 2460 vs 1607.
