claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 45 s | USD 0.007 | 2026-10-14 | project f769fdf+wk6ext | prompt 86aed168df9e

## 1. Root cause

There are two defects. The first explains the doubled credit card column and the 2460.00 total. The second explains the shortfalls on bank transfer, coupon, gift card, and part of credit card.

**a) Fan-out in `models/orders.sql` from duplicate rows in the `raw_payment_methods` seed.**

`raw_payment_methods` has a `processor` column and is described as "one row per payment method from the processor feed, with its processor and fee rate". So `credit_card` appears more than once, once per processor. `order_payments` left-joins `payments` to it on `payment_method` alone, so every credit card payment is duplicated before the `sum()`s run:

- `credit_card_amount` doubles: order 2 has one 20.00 credit card payment and shows 40.0. Order 13 has one 5.00 payment and shows 10.0. Order 54 has 18.00 and shows 36.0.
- `amount` (`sum(payments.amount)`) is computed over the same joined rows, so it doubles too: 853 × 2 + 754 = 2460.
- `fees` doubles for credit card. Order 2 shows 1.16 on 20.00 of credit card, which is two fee rows applied to the same payment.

`orders` stays unique on `order_id` because the join fans out payment rows, not order rows. That is why `unique_orders_order_id` passes while the amounts are wrong.

**b) NULL `amount` values in `raw_payments` (ingestion side).**

`stg_payments` keeps NULLs (`amount / 100` of NULL is NULL). The warehouse has 8 payments with no amount: 3 credit card, 3 bank transfer, 1 coupon, 1 gift card. Examples: payment 15 (order 13), payment 30 (order 25), payment 67 (order 58). Nothing in dbt flags them because `stg_payments` has no `not_null` test on `amount`, and `sum()` ignores NULLs silently.

These NULLs explain the gap. The settlement total is 1672.00 and the un-fanned staging total is 1607.00, a difference of 65.00. That equals 18 (credit card: 871 − 853) + 25 + 16 + 6. `region_revenue` and `customer_lifetime_value` total 1607.00 because they do not join through the fanned table, so they show the NULL problem only.

**What I verified and what I inferred:** The fan-out and its effect on amount and fees are confirmed by the sample rows. The NULLs are confirmed in the `stg_payments` sample and the counts. That the raw NULLs are the reason for the shortfalls is strongly indicated by the arithmetic, but I have not seen the raw rows or the ingestion job, so it is not proven. Confirm with `select * from raw_payments where amount is null`.

**Also affected:** `daily_revenue` inherits the inflated `orders.amount`. It is incremental and only rebuilds the last three days, so older days stay wrong until a full refresh.

## 2. Fix

**a) Make the join one row per payment method.** I can't tell from the files which processor's fee applies to each method, so this needs a decision from the data owner. Check the duplicates first:

```sql
select payment_method, processor, fee_rate from raw_payment_methods
where payment_method in (select payment_method from raw_payment_methods group by 1 having count(*) > 1);
```

Once you know the correct processor per method, filter the CTE in `orders.sql` explicitly:

```sql
payment_methods as (

    select payment_method, fee_rate
    from {{ ref('raw_payment_methods') }}
    where processor = 'REPLACE_WITH_SETTLEMENT_PROCESSOR'

),
```

Better still, fix the seed so it has one row per `payment_method` and keep `processor` only if it is used elsewhere. Then add the `unique` test below. Also select only the columns the join needs, so the seed's `processor` and `updated_at` cannot leak in later.

**b) Correct the 8 NULL amounts in `raw_payments`.** Fix them in the ingestion job or the source feed, using the processor settlement values. Do not `coalesce(amount, 0)` in dbt, since that would hide the problem.

**c) Rebuild everything, including history.** Because `daily_revenue` is incremental, run:

```
dbt build --full-refresh -s daily_revenue+
```

**Expected result after the fix:** `sum(orders.amount)` = 1672.00, `sum(credit_card_amount)` = 871.00, and order 2 shows credit card 20.00 and fee 0.58 at the single correct rate.

## 3. Tests that would have caught this

**a) Unique key on the seed** (`models/schema.yml`). This would have failed immediately on `credit_card`:

```yaml
seeds:
  - name: raw_payment_methods
    columns:
      - name: payment_method
        tests:
          - unique
          - not_null
```

**b) Not-null on payment amounts** (`models/staging/schema.yml`). This would have caught the 8 NULLs:

```yaml
  - name: stg_payments
    columns:
      - name: amount
        tests:
          - not_null
```

**c) Reconciliation test** (`tests/assert_orders_amount_matches_payments.sql`). This is the test that would have caught the 2460 vs 1607 gap directly. It compares totals across the staging and mart layers, so it catches fan-outs that unique keys miss:

```sql
with payments_total as (

    select sum(amount) as total from {{ ref('stg_payments') }}

),

orders_total as (

    select sum(amount) as total from {{ ref('orders') }}

)

select *
from payments_total
cross join orders_total
where abs(payments_total.total - orders_total.total) > 0.005
```

Note that this test alone would not have caught the NULLs, because both sides ignore NULLs. Test (b) covers that case. Once the settlement file is available, you can add a second reconciliation against the processor total as a seed or source.
