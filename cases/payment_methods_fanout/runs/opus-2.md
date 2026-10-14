claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 45 s | USD 0.170 | 2026-10-14 | project f769fdf+wk6ext | prompt 335d8f2d0911

# dbt debugging: duplicate `bank_transfer` row fans out the payments join

## 1. Root cause

**Files:** `models/orders.sql`, `order_payments` CTE, together with the warehouse table **`raw_payment_methods`**.

**What is wrong in the data:** `raw_payment_methods` has **two rows for `payment_method = 'bank_transfer'`**. They are probably two processors, or an old and a new row both kept. The `processor` column suggests a processor change was appended rather than replaced.

`orders.sql` does this before aggregating:

```sql
from payments
left join payment_methods
    on payments.payment_method = payment_methods.payment_method
group by payments.order_id
```

Each bank transfer payment matches two rows, so it is counted twice in both `bank_transfer_amount` and `total_amount`. The numbers fit this exactly:

- **Total:** 2083 − 1672 = 411, which is exactly the `bank_transfer` total in `stg_payments`.
- **By month:** the bank transfer column is 822 against 411, doubled in every month. The lookup has no date, so the fan-out applies to all history.
- **By order:**
  - Order 5: payment 17 shows as 34.
  - Order 10: 26 shows as 52.
  - Order 13: 14 shows as 28.
  - Order 25: 20 shows as 40, giving amount 78 instead of 58.
- **Other methods:** credit card, coupon and gift card each have one lookup row, so they are correct.
- **Other models:** `region_revenue` and `customers` read `stg_payments` without the lookup join, so they still total 1672.
- **Fees are wrong too.** Order 5 has 0.17 in fees on a 17.00 payment. That 1% is the *sum* of the two bank_transfer fee rates applied to the same payment. Credit card works out to about 2.9% (orders 13 and 54).

**Why no test caught it:**

- `raw_payment_methods.payment_method` only has `not_null`, with no `unique` test.
- The seed tests didn't run at all. The log shows "Found … 32 data tests", but only 28 ran. The 4 seed `not_null` tests were not executed because the seeds aren't part of this build.
- None of the tests checks that money is conserved between payments and orders.

To confirm the duplicate:

```sql
select payment_method, count(*), list(processor), list(fee_rate)
from raw_payment_methods group by 1 having count(*) > 1;
```

## 2. Fix

**Data:** remove the stale or duplicate `bank_transfer` row from `raw_payment_methods` at the ingestion or seed source. Finance or the payments team needs to confirm which processor and `fee_rate` is current. The current fees are a meaningless sum of both rates.

**Code:** make `orders.sql` robust so that a lookup table can never fan out money. Compute amounts from `payments` alone, and join the fee rate onto payments that already have one row each:

```sql
payment_fees as (

    select
        payments.order_id,
        payments.payment_method,
        payments.amount,
        coalesce(payment_methods.fee_rate, 0) as fee_rate

    from payments

    left join payment_methods
        on payments.payment_method = payment_methods.payment_method

),

order_payments as (

    select
        order_id,

        {% for payment_method in payment_methods -%}
        sum(case when payment_method = '{{ payment_method }}' then amount else 0 end) as {{ payment_method }}_amount,
        {% endfor -%}

        sum(amount) as total_amount,
        round(sum(amount * fee_rate), 2) as fees

    from payments_deduped_check  -- see note
    group by order_id

),
```

That version still fans out if the lookup has duplicates. The cleaner fix is to aggregate amounts with no join at all, and use a `unique` test to guarantee the lookup is one row per method:

```sql
order_payments as (

    select
        payments.order_id,

        {% for payment_method in payment_methods -%}
        sum(case when payments.payment_method = '{{ payment_method }}' then payments.amount else 0 end) as {{ payment_method }}_amount,
        {% endfor -%}

        sum(payments.amount) as total_amount,
        round(sum(payments.amount * coalesce(
            (select max(fee_rate) from payment_methods pm where pm.payment_method = payments.payment_method), 0
        )), 2) as fees

    from payments
    group by payments.order_id

),
```

My recommendation is to fix the data and add the tests below. A lookup with duplicate keys should **fail the build**, not be silently resolved in SQL.

**After fixing, rebuild the history:**

```bash
dbt build -s +orders+ --full-refresh
```

The `--full-refresh` matters. `daily_revenue` is incremental and only rebuilds the last 3 days, so every older date keeps the inflated revenue until it is fully refreshed.

## 3. Tests that would have caught it

**a) Key uniqueness on the lookup** in `models/schema.yml`, under `raw_payment_methods`:

```yaml
      - name: payment_method
        tests:
          - not_null
          - unique
```

Also make sure seed and source tests run in the nightly job. Either include them in the selector or declare these as `sources:` with tests. Right now 4 of the 32 tests are silently skipped. `raw_customer_regions` has the same exposure: it has an `updated_at` column, so a second row per customer would inflate `region_revenue`. Add `unique` on its `customer_id` as well.

**b) A reconciliation test** in `tests/assert_orders_amount_matches_payments.sql`. This catches any fan-out, whatever its cause:

```sql
-- every order's amount and per-method split must equal its payments in staging
with expected as (

    select
        order_id,
        sum(amount) as amount,
        sum(case when payment_method = 'bank_transfer' then amount else 0 end) as bank_transfer_amount
    from {{ ref('stg_payments') }}
    group by order_id

)

select
    orders.order_id,
    orders.amount,
    expected.amount as expected_amount
from {{ ref('orders') }} as orders
join expected
    on orders.order_id = expected.order_id
where abs(orders.amount - expected.amount) > 0.005
   or abs(orders.bank_transfer_amount - expected.bank_transfer_amount) > 0.005
```

With the current data, this test would have failed on all 33 bank transfer orders, including orders 5, 10, 13 and 25.
