claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 66 s | USD 0.009 | 2026-10-14 | project f769fdf+wk6ext | prompt 3077d0cba2a2

## 1. Root cause

Two separate defects in the raw payment data, both passing every test.

**A. Duplicate payment rows (credit card and bank transfer overstated).** `raw_payments` contains re-delivered copies of existing payments. Each copy has a new `id` (114+) but the same `order_id`, `payment_method`, and `amount` as an original:

| copy id | original id | order | method | amount |
|---|---|---|---|---|
| 114 | 1 | 1 | credit_card | 10.0 |
| 115 | 11 | 10 | bank_transfer | 26.0 |
| 116 | 21 | 18 | credit_card | 8.0 |
| 121 | 71 | 62 | gift_card | 0.14 |

Order 1 shows 20.00 because payment 1 and payment 114 both charge 10.00. The `unique` test on `stg_payments.payment_id` passes because the ids differ. Nothing tests the payment's business identity, so the duplicates flow through `stg_payments` → `orders.sql` untouched.

The sample shows only four copies (18 of credit card's 109 excess, 26 of bank transfer's 57). The rest presumably sit in ids beyond the sampled orders.

**B. Gift card amounts are off by a factor of 100 (likely; needs confirmation).** `stg_payments.sql` does `amount / 100` for every row. Credit card, bank transfer, and coupon rows are clearly cents: payment 1 is 1000 raw and 10.00 after conversion, matching the customer's charge. The gift card numbers don't fit that pattern:

- Gift card total in the model is 2.19. Removing the one visible duplicate (0.14) leaves 2.05.
- 2.05 is exactly 205.00 after multiplying by 100, which is the provider's figure.

That fits gift card amounts being stored in whole dollars in `raw_payments`, so dividing by 100 shrinks them by 100×. The duplicate sample can't explain this on its own, because duplicates only add value.

Reconciliation against finance:

| method | model | finance | gap |
|---|---|---|---|
| credit_card | 980.00 | 871.00 | +109.00 (duplicates) |
| bank_transfer | 468.00 | 411.00 | +57.00 (duplicates) |
| gift_card | 2.19 | 205.00 | −202.81 (unit mismatch, to confirm) |
| coupon | 185.00 | 185.00 | 0 |

Net: +109 + 57 − 202.81 = −36.81, which matches 1635.19 vs 1672.00.

**Files:**
- `jaffle_shop/models/staging/stg_payments.sql`: `amount / 100 as amount` applies one unit conversion to all methods.
- `jaffle_shop/models/staging/schema.yml`: tests only `unique` and `not_null` on `payment_id`.
- The ingestion job that loads `raw_payments`: it double-appends on re-run and loads gift card amounts in dollars.

## 2. Fix

**Step 1: find every duplicate and confirm it with the processor before deleting anything.**

```sql
select order_id, payment_method, amount,
       count(*) as n,
       list(id order by id) as ids
from raw_payments
group by 1, 2, 3
having count(*) > 1
order by order_id;
```

Check that the copies are re-deliveries (ids 114+ from a second load) and not real repeat charges. Then remove the copies and keep the lowest id. Fix the ingestion job so a re-run can't append the same batch again, ideally keyed on the processor's transaction reference.

**Step 2: fix the gift card units once confirmed.** Check a few gift card rows against the provider's line items (order 9 is raw 23 for gift card, for example). If the rows are dollars, change the conversion in `stg_payments.sql`:

```sql
case when payment_method = 'gift_card' then amount else amount / 100.0 end as amount
```

The better long-term fix is to normalize units at ingestion, so staging doesn't need per-method rules.

**Step 3: rebuild the downstream models.** `stg_payments` is a view, so the next run picks up changes. `orders`, `customers`, and `region_revenue` are tables and will rebuild. `daily_revenue` is incremental and only rebuilds the last three days, so older rows would keep the wrong values. Run:

```
dbt build --full-refresh -s daily_revenue
```

**Also check for fan-out risk:** `raw_payment_methods` and `raw_customer_regions` have only `not_null` tests. If either has more than one row per `payment_method` or `customer_id`, the joins in `orders.sql` and `region_revenue.sql` will multiply amounts. Add `unique` tests on those keys.

## 3. Test that would have caught this

Reconcile the model's totals per payment method against the processor's settlement statement. This is the check finance was doing by hand. It needs the statement loaded as a seed or source, e.g. `processor_settlement_totals(payment_method, total)`.

```sql
-- tests/assert_payments_match_settlement.sql
with ours as (
    select payment_method, round(sum(amount), 2) as total
    from {{ ref('stg_payments') }}
    group by 1
),

settlement as (
    select payment_method, total
    from {{ ref('processor_settlement_totals') }}
)

select
    coalesce(ours.payment_method, settlement.payment_method) as payment_method,
    ours.total as model_total,
    settlement.total as settlement_total
from ours
full outer join settlement using (payment_method)
where abs(coalesce(ours.total, 0) - coalesce(settlement.total, 0)) > 0.01
```

This test fails on the current data for credit card, bank transfer, and gift card. It would also catch future breakage from either cause.

A second, cheaper test would catch the duplicates directly. Use a singular test on `stg_payments` that fails when `(order_id, payment_method, amount)` repeats. It will flag real repeat charges too, so only add it after step 1 confirms the duplicates are re-deliveries.

**Caveats:** I worked only from the pasted output and couldn't query the warehouse. The gift card explanation is an inference from the totals and should be verified with the step 2 check. The sample doesn't show all duplicate rows, so step 1's query is needed to confirm the 109 and 57 totals.
