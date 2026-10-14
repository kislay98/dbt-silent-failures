claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 37 s | USD 0.196 | 2026-10-14 | project ad0b16b | prompt 1db8a43ee8e9

# Diagnosis

There are two separate data defects in the raw tables. Neither is a model logic change, and no test covers either one. The numbers in your report reconcile exactly once both are taken into account.

## 1. Root cause

### Defect A: duplicate `credit_card` row in `raw_payment_methods` doubles credit card amounts in `orders`

`raw_payment_methods` has 5 rows. The model expects 4, one per method, and the seed description in `models/schema.yml` says "One row per payment method":

```
credit_card │ stripe │ 0.029
credit_card │ adyen  │ 0.029
```

`models/orders.sql` joins `payments` to `payment_methods` on `payment_method` inside the `order_payments` CTE, before it aggregates. Every credit card payment therefore matches two rows and is summed twice. This inflates `credit_card_amount`, `amount` (`total_amount`) and `fees`.

- Order 2 was a 20.00 credit card payment and shows 40.00.
- Credit card non-null payments total 853.00. Doubled, that is the 1706.00 you see.

`customers` and `region_revenue` don't join this table, so they aren't doubled.

### Defect B: `raw_payments.amount` is NULL for payments the processor actually settled

The sample shows rows such as `id 30, order 25, coupon, NULL` and `id 55, order 49, credit_card, NULL`. The ingestion job loaded these payments without an amount. `sum()` skips NULLs and `else 0` hides them in the pivot, so these payments silently count as 0. Nothing tests `amount` for nulls.

- Order 13 has a 500-cent (5.00) credit card payment, which doubles to 10.00. The rest of its 19.00 settlement (14.00) sits in a NULL-amount row.

### Reconciliation

| | Value |
|---|---|
| Processor total | 1672.00 |
| Missing to NULL amounts: credit card 18 + bank transfer 25 + coupon 16 + gift card 6 | −65.00 |
| = what `customers` / `region_revenue` show | **1607.00** ✔ |
| + credit card counted a second time by the fan-out (853) | **2460.00** = `orders` ✔ |
| Credit card in `orders`: (871 − 18) × 2 | **1706.00** ✔ |

### Side effect on `daily_revenue`

`daily_revenue` is incremental and rebuilds only the last 3 days. Its history (total 1696) is therefore a mix of rows built before and after the duplicate appeared. It agrees with `orders` only for April. It needs a `--full-refresh` after the fix.

## 2. Fix

**Data (Defect B):** reload `raw_payments` from the source or processor for the NULL-amount rows, which should add 65.00 in total. Don't `coalesce(amount, 0)` in staging, because that would hard-code the under-reporting. Also fix the ingestion job so it doesn't drop amounts.

**Code (Defect A):** in `models/orders.sql`, collapse the processor feed to one row per method before joining. That keeps the model safe now that credit cards are routed through two processors:

```sql
payment_methods as (

    {#-
    Processor and fee rate per payment method, loaded as a seed like the other raw tables.
    A method can have several processors (credit_card: stripe, adyen), so collapse to one
    row per method before joining to payments to avoid fanning out payment amounts
    #}
    select
        payment_method,
        max(fee_rate) as fee_rate

    from {{ ref('raw_payment_methods') }}

    group by payment_method

),
```

`max` is exact today because both credit card processors charge 0.029. If their rates ever diverge, `raw_payments` will need a `processor` column and the join must be on `(payment_method, processor)`.

If one of the two credit card rows is simply stale, delete it from the feed as well. The code fix still guards against the next occurrence.

**Rebuild:**

```bash
dbt build --full-refresh -s orders+ daily_revenue
```

Expected results afterwards:

- `sum(orders.amount)` = 1672.00
- `credit_card_amount` = 871.00
- `customers` and `region_revenue` = 1672.00
- order 2 = 20.00, order 13 = 19.00

## 3. Tests that would have caught this

Add these to `models/schema.yml` and `models/staging/schema.yml`:

```yaml
# models/schema.yml (under seeds: raw_payment_methods)
      - name: payment_method
        tests:
          - not_null
          - unique          # failed: credit_card appears twice

# models/staging/schema.yml (under stg_payments)
      - name: amount
        tests:
          - not_null        # failed: settled payments loaded without an amount
```

Also add a reconciliation test that catches any fan-out or row loss between staging and marts, whatever the cause. Create it as `tests/assert_orders_amount_matches_payments.sql`:

```sql
-- fails if orders.amount doesn't sum to the payments it was built from
select
    (select sum(amount) from {{ ref('orders') }})       as orders_amount,
    (select sum(amount) from {{ ref('stg_payments') }}) as payments_amount
where abs(orders_amount - payments_amount) > 0.001
```

The `unique` test and this reconciliation test would each have failed on Defect A (2460 vs 1607). The `not_null` test would have failed on Defect B.
