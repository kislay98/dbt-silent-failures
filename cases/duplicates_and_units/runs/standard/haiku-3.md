claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 92 s | USD 0.014 | 2026-10-14 | project ad0b16b | prompt 922df6405b75

## Short answer

The dbt models compute correctly from their inputs. The bad numbers come from the `raw_payments` data that `stg_payments.sql` reads, which has two defects. The seed/raw data isn't in what you sent, so I can't name the exact rows. The arithmetic pins down both defects, and the queries in "Confirm first" will verify them.

## 1. Root cause

**The models aren't the problem.** `order_payments` is grouped by `order_id`, and `raw_payment_methods` has one row per method (4 rows), so the join in `orders.sql` doesn't fan out. The 1635.19 total agrees across `orders`, `customers`, and `region_revenue`, so every downstream model is faithfully passing the same bad input.

**Defect A: gift-card rows are in dollars, not cents.** `stg_payments.sql` divides every row by 100, which assumes cents.
- Coupons are in cents: order 58's coupon of 1800 becomes 18.00.
- Gift-card rows in the sample are 6, 23, 28, and 43, which become 0.06, 0.23, 0.28, and 0.43. Those are plausible as dollar amounts and implausible as cents. Dividing by 100 shrinks them 100x.
- This accounts for the gift-card gap: the stored total is 2.19, which is 219 raw, and finance shows 205.00.

**Defect B: card and bank-transfer payments are duplicated under new IDs.**
- Order 1 shows 20.00 for a 10.00 charge, so one charge is recorded twice.
- `payment_id` uniqueness passes, so the duplicates have distinct IDs.
- Coupons match exactly, so the duplication is specific to credit card and bank transfer. The excess is 109 on cards and 57 on bank transfers, and there are 125 payment rows for 99 orders.

**Reconciliation.** Correct the gift cards (+216.81) and remove the card and bank duplicates (−166):

1635.19 + 216.81 − 166.00 = 1686.00

That leaves a 14.00 residual against the 1672.00 settlement. It matches the gift-card gap (219 vs 205), so one gift-card row of 14.00 is probably a duplicate or a redemption the statement excludes. This needs checking.

**Why dbt missed it.** The tests check shape: `unique`, `not_null`, and `accepted_values`. None of them compare amounts to anything outside the warehouse.

**Seed tests never ran.** The models `ref()` the raw tables as seeds, but the log has no seed runs. The build found 32 tests and ran 28. The four missing are the seed tests on `raw_customer_regions` and `raw_payment_methods`. Confirm whether your warehouse `raw_payments` comes from the seed CSV or from the ingestion job, because dbt doesn't know what's in the table.

**Secondary: `daily_revenue` is stale.** It totals 1669.17 against 1635.19 in `orders`, and monthly figures differ in both directions while order counts match. The incremental model only rebuilds the last three days, so older dates keep values from before the data changed. A table this small gains nothing from being incremental.

**Doc drift:** `schema.yml` documents `total_order_amount` on `customers`, but the model produces `customer_lifetime_value`.

## Confirm first

```sql
-- A: unit check. If gift cards are in cents they should look like the other methods.
select payment_method, count(*), min(amount), median(amount), max(amount)
from raw_payments group by 1 order by 1;

-- B: possible duplicate charges (same order, method, and amount, different id)
select order_id, payment_method, amount, count(*) as n, list(id) as ids
from raw_payments group by 1, 2, 3 having count(*) > 1 order by 1;
```

## 2. Fix

Fix the data at the source, not in staging:
- **Ingestion/seed:** restore gift-card rows to cents (×100). Remove duplicate card and bank rows only after matching them to processor transaction references. Don't dedupe on (order, method, amount) alone, because genuine split payments can match. Resolve the 14.00 gift-card row the same way.
- **Stopgap until the feed is fixed:** make the unit explicit so the assumption is visible. This doesn't fix duplicates.

```sql
-- stg_payments.sql
-- gift_card rows arrive in dollars; every other method arrives in cents
case when payment_method = 'gift_card' then amount else amount / 100.0 end as amount
```

- **`daily_revenue`:** change it to `materialized='table'` (or drop the incremental), then run `dbt build -s daily_revenue --full-refresh`.
- **`schema.yml`:** rename `total_order_amount` to `customer_lifetime_value`.
- **Seeds:** run `dbt build` with seeds included so the four seed tests execute.

## 3. Test

A reconciliation test against the processor settlement would have failed on the first run. It needs the settlement file declared as a source:

```sql
-- tests/assert_payments_reconcile_to_settlement.sql
with ours as (
    select payment_method, sum(amount) as our_amount
    from {{ ref('stg_payments') }}
    group by payment_method
),
settled as (
    select payment_method, sum(amount) as settled_amount
    from {{ source('processor', 'settlements') }}
    group by payment_method
)
select payment_method, our_amount, settled_amount
from ours
full outer join settled using (payment_method)
where abs(coalesce(our_amount, 0) - coalesce(settled_amount, 0)) > 0.01
```

A warning-level duplicate check on `stg_payments` catches Defect B without blocking legitimate split payments:

```sql
-- tests/warn_possible_duplicate_payments.sql
{{ config(severity='warn') }}
select order_id, payment_method, amount
from {{ ref('stg_payments') }}
group by 1, 2, 3
having count(*) > 1
```

I haven't changed any files. I'd run the two confirmation queries and the settlement comparison before touching the ingestion job.
