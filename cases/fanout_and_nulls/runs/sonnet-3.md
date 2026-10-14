claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 42 s | USD 0.094 | 2026-10-14 | project f769fdf+wk6ext | prompt 86aed168df9e

## 1. Root cause

There are two independent problems. Together they explain every number you gave.

**A. Duplicate `credit_card` row in `raw_payment_methods`, fanned out by `models/orders.sql`**

The `order_payments` CTE does `payments left join payment_methods on payment_method` and then sums `payments.amount`. If the methods table has two rows for `credit_card`, every credit card payment is counted twice. I can't see the seed data, so the second row is inferred, but the evidence is strong:

- Order 2 has one 20.00 credit card payment in `stg_payments` and shows 40.00.
- Orders 18 and 54 double the same way, and order 25 doubles only its credit card part (22 → 44). Bank transfer (20) and coupon (18 on order 58) are untouched.
- `credit_card` in `orders` is 1706.00, which is 2 × 853.00 from `stg_payments`.
- `fees` are doubled for credit card too (order 2: 40 × 0.029 = 1.16).

`region_revenue` and `customers` read `stg_payments` directly, not through that join, so they are not doubled.

**B. NULL `amount` values in `raw_payments`**

Eight rows have a NULL amount: 3 `credit_card`, 3 `bank_transfer`, 1 `coupon` and 1 `gift_card`. They are payment ids 15, 21, 30, 62 and 67 in the sample, plus three more. Their value is missing from every output.

- `stg_payments` totals are 853 / 386 / 169 / 199 = 1607.00. That is the `region_revenue` and `customers` figure.
- The processor shows 871 / 411 / 185 / 205 = 1672.00.
- The shortfalls are 18 / 25 / 16 / 6, which is 65.00 across the NULL rows. This matches the bank transfer, coupon and gift card gaps you reported.
- Order 13 settled for 19.00, but its bank transfer payment (id 15) is NULL, leaving only the 5.00 credit card payment. The doubling turns that into 10.00. The missing amount there is 14.00.

The numbers reconcile exactly: A adds 853 to the order total, which gives 1607 + 853 = 2460. B removes 65 from the processor figure, which gives 1672 − 65 = 1607.

**Why no test failed**

- Nothing tests `stg_payments.amount` for NULLs. The `not_null` tests on `orders.*_amount` can't catch it, because `sum(case ... else 0)` is never NULL.
- Nothing tests that `payment_method` is unique in `raw_payment_methods`. Even its existing seed tests didn't run: the log shows 32 tests found but only 28 executed, so the 4 seed tests were skipped. The seeds are loaded by the ingestion job, not by `dbt build`.
- No test reconciles the output to the source.

## 2. Fix

**A. Data fix.** Remove the duplicate `credit_card` row from the methods table. Confirm it first:

```sql
select payment_method, count(*), list(processor), list(fee_rate)
from raw_payment_methods group by 1 having count(*) > 1;
```

If the two rows differ (for example, two processors with different rates), the feed needs a rule for which one applies. Fix this in ingestion, not in dbt.

To keep the model safe even if the feed breaks again, dedupe the methods CTE in `orders.sql`:

```sql
payment_methods as (

    select payment_method, max(fee_rate) as fee_rate  -- one row per method, so the join cannot fan out
    from {{ ref('raw_payment_methods') }}
    group by payment_method

),
```

I would rather have the unique test fail loudly than silently pick `max`. So I'd fix the data and add the test below, and treat the dedupe as optional.

**B. Backfill the 8 NULL amounts** from the processor statement (65.00 in total) and reload `raw_payments`. Don't `coalesce(amount, 0)` in staging, because that would hide the gap. Ingestion should reject or quarantine rows with a NULL amount.

**C. Rebuild `daily_revenue` fully.** It is incremental with a 3-day lookback anchored on `max(order_date)`. Older days keep the doubled and missing figures, so a normal run won't correct them:

```
dbt build --full-refresh -s daily_revenue+ orders+
```

The same weakness applies to the "payments can land a day or two late" comment: a late payment for an order older than 3 days is never picked up.

## 3. Tests

In `models/schema.yml`, add `unique` to the seed (and make sure the seed tests run in the nightly job, e.g. `dbt test -s raw_payment_methods raw_customer_regions` after ingestion):

```yaml
  - name: raw_payment_methods
    columns:
      - name: payment_method
        tests:
          - not_null
          - unique
```

In `models/staging/schema.yml`, add `not_null` to `stg_payments.amount`:

```yaml
      - name: amount
        tests:
          - not_null
```

Add a singular test that would have caught both problems, `tests/assert_orders_amount_matches_payments.sql`:

```sql
-- orders.amount must equal the sum of the payments feed, per order and in total
select o.order_id, o.amount, p.amount as payments_amount
from {{ ref('orders') }} o
join (
    select order_id, sum(amount) as amount
    from {{ ref('stg_payments') }}
    group by order_id
) p using (order_id)
where abs(o.amount - p.amount) > 0.005

union all

select null, o.total, p.total
from (select sum(amount) as total from {{ ref('orders') }}) o
cross join (select sum(amount) as total from {{ ref('stg_payments') }}) p
where abs(o.total - p.total) > 0.005
```

That test catches A (order 2: 40 vs 20). The `not_null` test on `stg_payments.amount` catches B. The two models also need to agree with each other, so I'd add the same kind of check between `region_revenue.revenue` and `orders.amount`.

**Latent risk:** `raw_customer_regions` has an `updated_at` column, which suggests history rows. It has no `unique` test on `customer_id`. `region_revenue` isn't fanned out today, since its total equals the `stg_payments` total, but add `unique` on `customer_id` (or filter to the latest row) before it becomes the next version of this bug.
