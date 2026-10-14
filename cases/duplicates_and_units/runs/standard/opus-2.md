claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 47 s | USD 0.218 | 2026-10-14 | project ad0b16b | prompt 922df6405b75

# Diagnosis: two data defects in `raw_payments`, not a model bug

The models are behaving as written. `stg_payments.sql` converts cents to dollars with `amount / 100`, and every downstream model only sums `stg_payments`. The bad numbers come from the `raw_payments` table that the ingestion job loads. It has two separate problems.

I inferred this from the outputs, not the raw rows, which weren't included. But the arithmetic reconciles to the cent, and the queries below will confirm it.

## 1. Root cause

**Defect A: gift card payments were loaded in dollars, not cents.**
Every gift card value in `orders` is a small fraction: order 9 shows 0.23, order 58 shows 0.06, and payment 68 shows 0.28. Every other method shows whole dollars. The ingestion job wrote gift card amounts as dollars (e.g. `23`), and `stg_payments.sql` divided them by 100 again. The raw gift card rows sum to 219, which becomes 2.19 after the division.

**Defect B: 12 duplicate payment rows, each with a new `id`.**
`raw_payments` has 125 rows for 99 orders, against the 113 expected. The duplicates have fresh `id`s, so `unique_stg_payments_payment_id` still passes. Order 1 is one of them: its 10.00 credit card payment appears twice, so it shows 20.00.

**The numbers reconcile exactly:**

| method | reported | statement | difference | explanation |
|---|---|---|---|---|
| credit_card | 980.00 | 871.00 | +109.00 | duplicates |
| bank_transfer | 468.00 | 411.00 | +57.00 | duplicates |
| gift_card | 2.19 | 205.00 | −202.81 | 219 dollars ÷ 100 = 2.19; 219 − 205 = 14 of duplicates |
| coupon | — | — | 0 | no duplicates, correct units |
| **total** | **1635.19** | **1672.00** | **−36.81** | = 109 + 57 − 202.81 |

So the duplicates add 180.00 (109 + 57 + 14), and the gift card unit error removes 216.81 (219 − 2.19).

**Confirm with:**
```sql
-- Defect B: expect 12 extra rows totalling 18000 (credit_card 10900, bank_transfer 5700, gift_card 14)
select order_id, payment_method, amount, count(*) as copies, list(id order by id) as ids
from raw_payments group by all having count(*) > 1 order by order_id;

-- Defect A: gift_card amounts will be ~100x smaller than the other methods
select payment_method, count(*), min(amount), max(amount), sum(amount)
from raw_payments group by 1 order by 1;
```

**Secondary issue:** `daily_revenue` is stale. Its monthly totals add up to 1669.17, which doesn't match `orders`. It is incremental with only a 3-day lookback, so a backfill or correction of older payments never reaches it.

## 2. Fix

Fix the data at the source. Then rebuild.

1. **Ingestion job:**
   - Write gift card amounts in cents, like the other methods.
   - Make the load idempotent, either by upserting on the source payment key or by truncating and reloading. The duplicates look like an append that was retried or ran twice.
2. **Repair the loaded data.** Back up `raw_payments` first, then use the `ids` from the query above:
   ```sql
   delete from raw_payments
   where id in (/* the later id of each duplicate pair from the query above */);

   update raw_payments set amount = amount * 100 where payment_method = 'gift_card';
   ```
   Only run the gift card update once. Running it again would multiply those amounts by 100 a second time.
3. **Rebuild everything, including the incremental model:**
   ```
   dbt build --full-refresh
   ```
   Expected results: `sum(orders.amount)` = 1672.00, credit_card = 871.00, bank_transfer = 411.00, gift_card = 205.00, order 1 = 10.00.

I'd avoid patching `stg_payments.sql` with a special case like `case when payment_method = 'gift_card' then amount ...`. It would silently break the day ingestion is fixed. A generic dedupe in staging is also risky, because a real order can legitimately have two equal payments by the same method.

## 3. Tests that would have caught this

Add these to `models/staging/schema.yml` on `stg_payments`. They need the `dbt_utils` package.

```yaml
  - name: stg_payments
    tests:
      # duplicate rows re-keyed with a new id
      - dbt_utils.unique_combination_of_columns:
          arguments:
            combination_of_columns: ['order_id', 'payment_method', 'amount']
          config:
            severity: error
    columns:
      - name: amount
        tests:
          # catches dollar-valued rows divided by 100 again
          - dbt_utils.accepted_range:
              arguments:
                min_value: 1
              config:
                severity: warn
```

Add a reconciliation test against the processor statements. This is the one that catches any problem of this kind. Load the statement totals as a seed, `seeds/processor_settlements.csv`, with columns `payment_method,amount`. Then add `tests/assert_payments_reconcile_to_settlements.sql`:

```sql
with ours as (
    select payment_method, round(sum(amount), 2) as amount
    from {{ ref('stg_payments') }}
    group by 1
)
select s.payment_method, s.amount as settled, o.amount as ours
from {{ ref('processor_settlements') }} s
full outer join ours o using (payment_method)
where coalesce(o.amount, 0) <> coalesce(s.amount, 0)
```

The current tests only check keys, nulls, and enum values. That's why the build was all green while the totals were wrong. Only a test on amounts compared to an external source of truth would have turned this build red.
