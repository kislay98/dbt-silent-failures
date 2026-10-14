claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 64 s | USD 0.009 | 2026-10-14 | project f769fdf+wk6ext | prompt 3077d0cba2a2

## 1. Root cause

Two separate problems in the data, and the project passes both through without checking.

**a) Duplicated payment rows in `raw_payments` (credit card, bank transfer, a little gift card).** The sample shows the same payment loaded twice under different ids, with the second copy at ids 114 and above:

| order_id | payment_method | amount | payment_ids |
|---|---|---|---|
| 1 | credit_card | 10.00 | 1, **114** |
| 10 | bank_transfer | 26.00 | 11, **115** |
| 18 | credit_card | 8.00 | 21, **116** |
| 62 | gift_card | 0.14 | 71, **121** |

Order 1 shows 20.00 because payment 114 is a copy of payment 1. The ingestion job appears to have replayed a batch with new ids, so `payment_id` uniqueness still holds.

This passes through `jaffle_shop/models/staging/stg_payments.sql`, which only divides `amount` by 100. `jaffle_shop/models/staging/schema.yml` only tests `payment_id` for uniqueness and `payment_method` for accepted values, so the duplicates pass. The sample accounts for only 44.14 of the 166 overstatement (credit card +109, bank +57), so the rest of the duplicates are not visible here.

**b) Gift card: about 202.81 is missing from `raw_payments` entirely.** The sample's gift card rows sum to exactly 2.19 across the 12 orders shown, and the grouped query shows only the four valid methods with no NULL group. So nothing in the project drops or rescales gift card rows: `stg_payments` is a pass-through, and `orders.sql` is a left join with conditional sums. The ~203 is not in the warehouse data this project sees. The likely causes are a missing or truncated gift card batch in the ingestion job, or redemptions that live in a different source than the one loaded. I can't confirm which from these tables.

**Why every test passed:** the duplicate rows have unique `payment_id`s, and the gift card gap leaves every row individually valid. Nothing checks totals against the processor, and the seed tests on `raw_payment_methods` and `raw_customer_regions` don't appear in the log, so they didn't run in this build.

**Arithmetic check:** 1635.19 (output) + 109 + 57 − 202.81 = 1672.00 (finance) for the all-method total. Coupons match, as expected since no coupon duplicates appear.

**Ruled out:** the `raw_payment_methods` join in `orders.sql` does not fan out payments. If it did, `orders.amount` would exceed the 1635.19 raw total, and it equals that total exactly.

## 2. Fix

Verify first. Run these against the warehouse:

```sql
-- a) duplicated payments: same order, method and amount, different ids
select order_id, payment_method, amount, count(*) as copies, min(id) as first_id, max(id) as last_id
from raw_payments
group by all
having count(*) > 1
order by order_id;

-- b) gift card rows, and whether the batch load counts match the ingestion manifest
select id, order_id, amount from raw_payments where payment_method = 'gift_card' order by id;
```

Then fix at the source, not in dbt:

- **Duplicates:** check the matches against the processor statement. Where a second copy is a replay, remove it in the ingestion job, keyed on the processor's transaction reference. The raw table has no such column, so I would not add a SQL dedupe in `stg_payments.sql` yet. A `qualify row_number()` on (order_id, payment_method, amount) would also drop legitimate split payments of the same amount.
- **Gift card:** reload the missing gift card rows from the provider's redemption statement, and check the ingestion job's per-method row counts for this load.

After the reload, the totals should match finance: credit card 871.00, bank transfer 411.00, gift card 205.00, and the overall 1672.00.

## 3. Test that would have caught this

Add a singular test that reconciles staging totals against processor control totals. This catches both problems, including the gift card gap, which no key-level test would find. It needs a `raw_processor_totals` seed (payment_method, total_amount) loaded from the processor statement:

```sql
-- tests/assert_payment_totals_match_processor.sql
with actual as (
    select payment_method, round(sum(amount), 2) as total
    from {{ ref('stg_payments') }}
    group by payment_method
),
expected as (
    select payment_method, round(total_amount, 2) as total
    from {{ ref('raw_processor_totals') }}
)
select coalesce(actual.payment_method, expected.payment_method) as payment_method,
       actual.total as actual_total,
       expected.total as expected_total
from actual
full join expected using (payment_method)
where actual.total is distinct from expected.total
```

Add a second test for the duplicate rows, which would have caught the 4 cases directly. It may need `severity: warn` at first, since genuine split payments can share an amount:

```sql
-- tests/assert_no_duplicate_payments.sql
select order_id, payment_method, amount, count(*) as copies
from {{ ref('stg_payments') }}
group by 1, 2, 3
having count(*) > 1
```

Also add `unique` on `raw_payment_methods.payment_method` in `jaffle_shop/models/schema.yml`. That join is only safe today because the seed has one row per method. Make sure the seed tests run in the nightly build; they are missing from this log.
