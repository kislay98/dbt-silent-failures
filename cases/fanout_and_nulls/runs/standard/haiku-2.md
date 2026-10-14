claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 78 s | USD 0.012 | 2026-10-14 | project ad0b16b | prompt 1db8a43ee8e9

## 1. Root cause

**Primary cause: duplicate keys in `raw_payment_methods` fan out the join in `jaffle_shop/models/orders.sql`.**

The raw table has two rows for `credit_card` (`stripe`, 0.029 and `adyen`, 0.029). The `order_payments` CTE left-joins `payments` to `payment_methods` on `payment_method`, so every credit-card payment is returned twice. `sum(payments.amount)` double-counts it, and so do `credit_card_amount` and `fees`. Nothing catches this: the seed's `schema.yml` only has `not_null` on `payment_method`.

The numbers confirm it:
- Output credit card = 1706 = 2 × 853. The true warehouse credit-card total is 853.
- Output `amount` = 2460 = 1607 + 853. The 1607 is the true payments total, which matches `region_revenue` and `customers.customer_lifetime_value`.
- Order 2: charged 20, shows 40 (2 × 20).
- Order 13: shows 10.00, which is 2 × 5.00 (payment id 14, credit_card, 500 cents). The 19.00 settlement implies about 9.00 of payments still missing.
- `fees` is doubled for credit card too.

**Second, separate cause: about 65.00 of settled payments never reach the output.** After removing the fan-out, the warehouse total is 1607 against 1672 settled. The gap is credit card 18, bank transfer 25, coupon 16, gift card 6. The join doesn't cause this. Two likely causes:
- **NULL amounts in `raw_payments`.** The sample has two: payment 30 (coupon, order 25) and payment 55 (credit_card, order 49). `sum()` skips NULLs silently, and `stg_payments` has no `not_null` test on `amount`.
- **Orphan payments**, whose `order_id` has no row in `raw_orders`. `orders.sql` joins from orders, so these disappear.

I can't see the full table, so I can't confirm which, or both. The queries in the verification section below will.

**Third: `daily_revenue` is stale.** Its revenue totals 1696 while `orders.amount` totals 2460, even though both have 99 orders. It's incremental and only rebuilds the last three days, so older rows were written before the duplicate existed. Fixing upstream won't correct them without a full refresh.

**Why every test passed:**
- The seed tests never ran. The log says "Found 5 seeds" but no seed is built or tested, and none of the 28 data tests touch `raw_*`.
- The amount tests are `not_null`, and a components-equal-total check wouldn't catch the fan-out, because `amount` and `credit_card_amount` are both doubled.

**Files and data involved:**
- `jaffle_shop/models/orders.sql`: the `order_payments` join (the fan-out)
- `raw_payment_methods` (warehouse table, seed CSV in `seeds/`): duplicate `credit_card` key
- `raw_payments`: NULL `amount` values, and possibly orphan `order_id`s
- `jaffle_shop/models/daily_revenue.sql`: stale history from the incremental strategy

**Verification queries:**
```sql
select payment_method, count(*) from raw_payment_methods group by 1 having count(*) > 1;

select count(*) as total, count(*) filter (where amount is null) as null_amounts from raw_payments;

select p.* from raw_payments p left join raw_orders o on o.id = p.order_id where o.id is null;
```

## 2. Fix

1. **Fix the data at the source.** The ingestion job should write one row per `payment_method`. Both `credit_card` rows have fee_rate 0.029, so deduplicating is safe for fees today. The payments table has no `processor` column, though, so the fee can't be attributed per processor. If the rates diverge, add `processor` to `raw_payments` and join on it. Don't dedupe silently in the model, because that hides the problem the tests should flag.
2. **Fix NULL amounts at ingestion.** Don't `coalesce` to 0 in `stg_payments`, since that hides the gap.
3. **Orphan payments:** fix at ingestion, or make the reconciliation test below fail on them.
4. **`orders.sql` needs no logic change** once the mapping is unique. The existing join is correct then.
5. **Rebuild with a full refresh:** `dbt build --full-refresh`, so `daily_revenue` is rebuilt from scratch.

Expected after 1 and 5: `orders.amount` = 1607, credit card = 853, and `daily_revenue` sums to 1607. The 65.00 gap to settlement remains until items 2 and 3 are resolved.

## 3. Tests that would have caught it

- **`unique` on `payment_method` for `raw_payment_methods`.** This catches the fan-out directly and fails the build rather than deduplicating silently.
- **Move raw-table tests onto `sources`** (in a `sources.yml`). The ingestion job loads these tables and the seed tests never run in this build, so the checks have to live where `dbt build` executes them.
- **`not_null` on `stg_payments.amount`.** This catches the NULL amounts.
- **Reconciliation: each order's `amount` equals the sum of its payments.** This catches the fan-out, since `amount` is doubled against the payments:
  ```sql
  -- tests/assert_order_amount_matches_payments.sql
  select o.order_id, o.amount, p.paid
  from {{ ref('orders') }} as o
  join (
      select order_id, sum(amount) as paid
      from {{ ref('stg_payments') }}
      group by order_id
  ) as p on p.order_id = o.order_id
  where abs(o.amount - p.paid) > 0.005
  ```
- **Orphan check: every payment has an order.**
  ```sql
  -- tests/assert_payments_have_orders.sql
  select p.payment_id
  from {{ ref('stg_payments') }} as p
  left join {{ ref('stg_orders') }} as o using (order_id)
  where o.order_id is null
  ```
- **Strongest check:** reconcile per-method totals against the processor settlement file each night. That's the comparison finance actually runs, and it would have flagged all three problems.

**What I haven't verified:** I haven't seen the seed CSVs or the nightly job config. Please confirm whether the nightly build excludes seeds (for example, `--exclude resource_type:seed`). If it does, the raw-table tests have never run in production.
