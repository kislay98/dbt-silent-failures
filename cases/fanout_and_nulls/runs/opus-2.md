claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 50 s | USD 0.177 | 2026-10-14 | project f769fdf+wk6ext | prompt c0748c4eed7e

# Diagnosis

There are two independent data problems. Neither one breaks a `not_null` or `unique` test on the models.

## 1. Root cause

### A. `raw_payment_methods` has two rows for `credit_card`, which doubles every card payment

In `models/orders.sql`, the `order_payments` CTE joins every payment to `raw_payment_methods` on `payment_method` before aggregating. If `credit_card` appears twice (probably one row per processor, since the table has a `processor` column), every card payment is counted twice:

| Evidence | Value | Explanation |
|---|---|---|
| Order 2 | one card payment of 20.00 | shows `credit_card_amount` = 40.00 |
| Order 2 fees | 1.16 | = 20 × 0.058, two fee rates summed |
| Order 54 | 18.00 card payment | shows 36.00, fees 1.04 = 18 × 0.058 |
| Card column | 1706.00 | = 2 × 853.00 (the `stg_payments` card total) |

The other methods aren't duplicated, so their columns aren't inflated.

`region_revenue` and `customers` never join `raw_payment_methods`. That's why they show 1607.00, the true sum of `stg_payments`.

### B. `raw_payments` has 8 rows with `amount` NULL, which loses 65.00

`stg_payments` shows `count(*)` ≠ `count(amount)` for every method:

| Method | NULL rows | Short vs processor |
|---|---|---|
| bank_transfer | 3 | 25.00 |
| coupon | 1 | 16.00 |
| credit_card | 3 | 18.00 (871 − 853) |
| gift_card | 1 | 6.00 |

The checks reconcile:
- 1607 + 65 = 1672, the processor settlement.
- 2460 = 1672 − 65 + 853, the true total minus the NULLs plus the duplicated card total.

`sum()` silently skips NULLs, and the `else 0` branch makes every `*_amount` column non-null. So all the `not_null` tests on `orders` pass. Order 13, for example, is 5.00 card plus a NULL bank transfer (14.00 per the 19.00 settlement). The 5.00 card payment is doubled, so it shows 10.00.

I can't see why those 8 values are NULL. A likely cause is the ingestion job failing to cast them into the `INTEGER` column, for example `1400.00` or an empty string. The ingestion team should check the source rows for those payment IDs.

### Why no test fired

- `raw_payment_methods.payment_method` only has `not_null`, no `unique`.
- `stg_payments.amount` has no test at all.
- The build log shows 7 models and 28 of the 32 tests ran. The 4 seed tests were never executed, because seeds are excluded from the nightly build (ingestion loads them). So even the existing tests on the raw tables don't run.

## 2. Fix

**Data:**

1. Reduce `raw_payment_methods` to one row per `payment_method`. Ask finance which processor and fee rate is current for `credit_card`; don't pick one arbitrarily.
2. Reload the 8 NULL `raw_payments.amount` values from the processor statement, in cents. Do **not** `coalesce(amount, 0)` in staging, because that just makes the shortfall permanent and invisible.
3. Rebuild. `daily_revenue` is incremental with a 3-day lookback, so its historical rows still hold the inflated numbers and need a full refresh:
   ```
   dbt build --full-refresh -s daily_revenue+
   ```
   Or simply run `dbt build --full-refresh`.

**Code hardening (recommended):** in `orders.sql`, compute the money columns from `payments` alone. Then a bad lookup table can only affect `fees`, never amounts:

```sql
order_amounts as (

    select
        order_id,
        {% for payment_method in payment_methods -%}
        sum(case when payment_method = '{{ payment_method }}' then amount else 0 end) as {{ payment_method }}_amount,
        {% endfor -%}
        sum(amount) as total_amount
    from payments
    group by order_id

),

order_fees as (

    select
        payments.order_id,
        round(sum(payments.amount * coalesce(payment_methods.fee_rate, 0)), 2) as fees
    from payments
    left join payment_methods
        on payments.payment_method = payment_methods.payment_method
    group by payments.order_id

),
```

Then join both CTEs to `orders` in `final`.

Also consider declaring the raw tables as **sources** rather than seeds, since ingestion loads them. Their tests would then run in the nightly `dbt build` instead of being silently skipped with the seeds.

## 3. Tests that would have caught it

In the schema files (on sources, or make sure the seed tests are actually selected):

```yaml
  - name: raw_payment_methods
    columns:
      - name: payment_method
        tests:
          - unique        # catches A
          - not_null
```

```yaml
  - name: stg_payments
    columns:
      - name: amount
        tests:
          - not_null      # catches B
```

Add a reconciliation test, `tests/assert_orders_amount_reconciles_to_payments.sql`, which catches any future fan-out regardless of cause:

```sql
-- orders.amount must equal the payments it is built from
select
    (select sum(amount) from {{ ref('orders') }})       as orders_total,
    (select sum(amount) from {{ ref('stg_payments') }}) as payments_total
where abs(
    coalesce((select sum(amount) from {{ ref('orders') }}), 0)
  - coalesce((select sum(amount) from {{ ref('stg_payments') }}), 0)
) > 0.01
```

This returns 2460 vs 1607 on the current data. The `not_null` test on `stg_payments.amount` fails with 8 rows. Together they cover both defects.

Small unrelated issue: `models/schema.yml` documents a `total_order_amount` column on `customers`, but the model outputs `customer_lifetime_value`.
