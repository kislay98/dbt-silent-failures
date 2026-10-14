claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 28 s | USD 0.004 | 2026-10-14 | project 7d5b525+dirty

## 1. Root cause

The bug is in the data, not the models. **13 rows in `raw_payments` reference `order_id` values that don't exist in `raw_orders`**, and they total exactly the gap:

- `sum(stg_payments.amount)` = 1672.00
- `sum(orders.amount)` = 1532.00
- Orphaned payments: 13 rows, 140.00 total
- 1672.00 − 140.00 = 1532.00

`orders.sql` builds `order_payments` from `stg_payments`, then left-joins it onto `stg_orders`. A payment with no matching order is never attached to anything, so its money disappears. `region_revenue.sql` has the same problem: it inner-joins payments to orders, so the same 140.00 is dropped there too. `daily_revenue` inherits the undercount from `orders`.

Order 25 fits this pattern. It shows 42.00 against 58.00 paid, a 16.00 shortfall. That is consistent with one of its payments carrying a bad `order_id` and landing in the orphan set. I haven't confirmed this, because I can't see the raw rows. The query in section 2 will confirm it.

**Why every test passes:** nothing checks that a payment maps to an order. `stg_payments.order_id` has no `relationships` test (only `not_null`-style coverage exists for `orders.customer_id`), and `orders.amount` only has `not_null`, which can't detect undercounting.

**Ruled out:**
- **Cents conversion.** `amount / 100` on INTEGER returns DOUBLE in DuckDB, so there is no truncation. The 1672.0 total confirms the scaling is right.
- **Region join fan-out.** `region_revenue` revenue ties exactly to `orders`, so no duplicate `customer_id` rows in the regions seed are inflating it. (That seed does lack a uniqueness test on `customer_id`, which is worth adding.)

## 2. Fix

**Step 1: Identify the orphans.**

```sql
select p.order_id, count(*) as payments, sum(p.amount) / 100 as amount
from raw_payments p
left join raw_orders o on p.order_id = o.id
where o.id is null
group by 1
order by 1;
```

Then check whether order 25's missing 16.00 is among them:

```sql
select * from raw_payments where order_id = 25 or order_id in (<orphan ids>);
```

**Step 2: Correct the source.** No model change can fix this honestly. The orphan payments have no `customer_id` or order date, so `orders` can't be extended to include them without inventing data. Resolve each case upstream:
- If a payment's `order_id` was mistyped (e.g., order 25's 16.00 payment), correct it in `raw_payments`.
- If the order itself is missing from the extract, add the order to `raw_orders`.

Confirm the correct values with whoever owns the payment/order feed before editing the seeds.

**Step 3: Rebuild, including history.** `daily_revenue` only rebuilds the last three days, so fixing the data won't correct older dates. Run:

```
dbt build --full-refresh -s daily_revenue+
```

**Step 4: Make silent drops fail loudly.** Add the test from section 3 so this can't recur quietly.

Once the data is fixed, `sum(orders.amount)` should equal 1672.00, and `region_revenue` should match too (provided every customer has a region in the seed).

## 3. Test that would have caught it

**Referential test on payments → orders** in `models/staging/schema.yml`:

```yaml
  - name: stg_payments
    columns:
      - name: payment_id
        tests:
          - unique
          - not_null
      - name: order_id
        tests:
          - not_null
          - relationships:
              arguments:
                to: ref('stg_orders')
                field: order_id
      - name: payment_method
        tests:
          - accepted_values:
              arguments:
                values: ['credit_card', 'coupon', 'bank_transfer', 'gift_card']
```

This fails with 13 errors on the current data.

**Reconciliation test** in `tests/assert_orders_reconcile_to_payments.sql`. This catches any future leak, not just orphans:

```sql
-- Fails (returns rows) if the orders mart does not account for every payment dollar
select
    paid.total as paid_total,
    ordered.total as ordered_total
from (select sum(amount) as total from {{ ref('stg_payments') }}) as paid
cross join (select sum(amount) as total from {{ ref('orders') }}) as ordered
where abs(paid.total - ordered.total) > 0.005
```

This also fails on the current data, which is the correct outcome until the source is fixed.
