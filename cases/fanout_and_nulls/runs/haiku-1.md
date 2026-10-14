claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 61 s | USD 0.008 | 2026-10-14 | project f769fdf+wk6ext | prompt 86aed168df9e

## 1. Root cause

Two separate defects, both in the raw data, and neither is caught by the current tests.

**A. Fan-out in `orders.sql` from duplicate `credit_card` rows in `raw_payment_methods`.** The `payment_methods` CTE joins `raw_payment_methods` on `payment_method` alone, but that seed has a `processor` column, so `credit_card` probably appears twice (one row per processor). Each credit-card payment then joins to two rows, and every `sum(...)` in `order_payments` counts it twice.

The output confirms this. Order 2 has one 20.00 payment and shows 40.00 credit card. Its fee is 1.16, which is exactly 20 × 0.029 × 2, so both duplicate rows must carry fee_rate 0.029. The doubling also explains the totals: credit card in `orders` is 1706 = 2 × 853, and `orders.amount` is 2460 = 1607 + 853.

**B. Eight NULL `raw_payments.amount` values.** `stg_payments` does `amount / 100`, which keeps NULLs. Nothing downstream rejects them: `sum()` skips them silently, and the `not_null` tests on `orders.*_amount` pass only because the `else 0` in the CASE expressions turns missing values into zeros. The row counts show 113 payments but only 105 with an amount. The shortfalls match the missing values exactly:

| method | `stg_payments` total | `orders` (current) | processor | short |
|---|---|---|---|---|
| credit_card | 853 | 1706 (fan-out) | 871 | 18 |
| bank_transfer | 386 | 386 | 411 | 25 |
| coupon | 169 | 169 | 185 | 16 |
| gift_card | 199 | 199 | 205 | 6 |
| total | 1607 | 2460 | 1672 | 65 |

Order 13 fits both defects: its credit-card payment of 5.00 shows as 10.00 (fan-out), and its bank-transfer payment (id 15) is NULL, so the 14.00 is missing. Together that gives 10 against 19.

**Downstream impact:**
- `orders.amount`, the `*_amount` columns, `fees`, and `daily_revenue.revenue` are affected by both defects.
- `region_revenue` and `customers.customer_lifetime_value` don't join the seed, so they are affected only by the NULLs. That is why both total 1607.
- `daily_revenue` is incremental and only rebuilds the last three days, so older days stay wrong until a full refresh.
- The log has 28 data tests, but 32 were found. The four missing tests are the `raw_customer_regions` and `raw_payment_methods` seed tests, so nothing tested the seed in this run.

I haven't run queries against the warehouse. The contents of `raw_payment_methods` and the IDs of the NULL rows are inferred from the output, so please confirm them with the queries below.

## 2. Fix

**Data (primary fix):**

```sql
select * from raw_payment_methods order by payment_method;
```

Keep one row per `payment_method`. Choose the processor that is actually live. Fees don't change between the two duplicate rows, since both use 0.029.

```sql
select id, order_id, payment_method from raw_payments where amount is null order by id;
```

Get the eight amounts from the processor settlement and re-ingest them. Don't type values into the raw tables by hand. If the settlement can't supply them, that is a decision for finance, not a code change.

**Model guard (`orders.sql`):** make the join safe even if the seed gets another duplicate:

```sql
payment_methods as (

    select payment_method, fee_rate
    from {{ ref('raw_payment_methods') }}
    qualify row_number() over (partition by payment_method order by processor) = 1

),
```

Treat this as a stopgap. The uniqueness test below is what should catch a bad seed.

**Rebuild:** run `dbt build --full-refresh` so `daily_revenue` gets its history rebuilt.

**Expected results after the fix:** `sum(orders.amount)` = 1672, credit card = 871, bank transfer = 411, coupon = 185, gift card = 205, order 2 = 20.00, order 13 = 19.00, and `region_revenue` and CLV both total 1672.

## 3. Tests that would have caught this

**a. Uniqueness on the seed (`models/schema.yml`, `seeds:` section):**

```yaml
  - name: raw_payment_methods
    columns:
      - name: payment_method
        tests:
          - unique
          - not_null
```

This fails on the duplicate `credit_card` row.

**b. Non-null on payment amounts (`models/staging/schema.yml`):**

```yaml
  - name: stg_payments
    columns:
      - name: amount
        tests:
          - not_null
```

This fails on the eight NULL amounts and stops the build, rather than quietly reporting 1607.

**c. Reconciliation test (`tests/assert_order_amount_matches_payments.sql`):**

```sql
-- an order's amount must equal the sum of its payments
with payment_totals as (

    select order_id, sum(amount) as total_amount
    from {{ ref('stg_payments') }}
    group by order_id

)

select orders.order_id, orders.amount, payment_totals.total_amount
from {{ ref('orders') }} as orders
join payment_totals on orders.order_id = payment_totals.order_id
where abs(orders.amount - payment_totals.total_amount) > 0.005
```

This fails on order 2 (40 against 20) and every other order with a credit-card payment.

**d. Run the seed tests in the nightly build.** The log shows 28 of 32 data tests running. Check the scheduler's selection so the seed tests aren't excluded.
