claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 68 s | USD 0.010 | 2026-10-14 | project ad0b16b | prompt 1db8a43ee8e9

## 1. Root cause

Two data defects flow into `orders`, and a third problem hides them from the tests.

**A. Duplicate `credit_card` row in the fee feed fans out `models/orders.sql`.**
`raw_payment_methods` has two rows for `credit_card` (`stripe`, 0.029, and `adyen`, 0.029). The `order_payments` CTE left-joins `payments` to it on `payment_method` with no dedupe, so every credit-card payment appears twice. The `credit_card_amount`, `total_amount` (exposed as `amount`), and `fees` sums then double for those payments.

**B. NULL payment amounts in `raw_payments` are dropped silently.**
The raw sample has NULL amounts: payment 30 (coupon, order 25) and payment 55 (credit_card, order 49). `models/staging/stg_payments.sql` computes `amount / 100`, which passes NULL through, and `sum()` ignores NULLs. `models/staging/schema.yml` has no `not_null` on `stg_payments.amount`. The `not_null` tests on `orders` check the aggregate, which is non-null as long as one payment exists.

**C. The tests that would catch A never ran.**
The log says `Found ... 32 data tests`, but the run executes 28 (35 = 7 models + 28 tests). The 4 missing tests are the `raw_customer_regions` and `raw_payment_methods` seed tests, and no seed runs appear in the log. The nightly build therefore never checks the fee feed. Nothing tests `payment_method` for uniqueness anywhere.

**D. `daily_revenue` is stale.**
Monthly revenue there (496 / 415 / 622) doesn't match `orders` (668 / 679 / 950), though order counts match. The incremental model only rebuilds the last three days, so older rows keep values from earlier runs. The tests check only `unique` and `not_null`, so they pass.

**The numbers reconcile.** Let CC = true credit-card total and N = everything else, both from warehouse payments:

| | Warehouse (`orders`) | Processor | Gap |
|---|---|---|---|
| credit_card | 1706 = 2 × 853 | 871 | −18 |
| bank + coupon + gift | 754 | 801 | −47 (25 + 16 + 6) |
| total | 2460 | 1672 | −65 |

- `orders` total 2460 = N + 2·CC, and `customers` / `region_revenue` total 1607 = N + CC. Solving gives CC = 853 and N = 754, so A fully explains the overstatement.
- The 1607 vs 1672 gap is exactly the 65 missing from NULL amounts (B).
- Order 2 (charged 20, shows 40) is the doubling in A. Order 13 (settled 19, shows 10) matches payment 14 (500 cents) doubled, with the other 9.00 likely a NULL-amount payment not in the sample.

Confirm with these queries (I only have a sample, so I haven't verified every row):

```sql
select payment_method, count(*) from raw_payment_methods group by 1 having count(*) > 1;
select id, order_id, payment_method from raw_payments where amount is null;
```

## 2. Fix

**a. Feed and model (`models/orders.sql`).** Fix the feed so each `payment_method` appears once. As a guard in the model, collapse to one row per method:

```sql
payment_methods as (

    {#-
    One row per payment method. The feed currently has two credit_card rows (stripe, adyen).
    #}
    select payment_method, max(fee_rate) as fee_rate
    from {{ ref('raw_payment_methods') }}
    group by payment_method

),
```

Before merging, confirm with finance whether credit cards settle through both Stripe and Adyen. If they do, fees belong per payment, which needs a processor column on `raw_payments`. The seed alone can't say which processor handled a given payment.

**b. NULL amounts.** Re-pull the NULL amounts from the processor (ids 30, 55, and any others from the query above) and fix the ingestion job. Don't `coalesce(amount, 0)` in dbt, since that hides the gap again.

**c. Rebuild.** After the source fixes, run `dbt build --full-refresh --select daily_revenue+ orders+`, or simply `dbt build --full-refresh` if you want everything rebuilt. The full refresh is what corrects the stale Jan–Mar rows in `daily_revenue`. Also make sure the nightly run includes the seeds, or the feed tests will keep being skipped.

## 3. Tests that would have caught it

**a. Schema tests** (`models/schema.yml` and `models/staging/schema.yml`):

```yaml
# seeds: raw_payment_methods
      - name: payment_method
        tests:
          - not_null
          - unique          # would have failed on the duplicate credit_card row

# staging: stg_payments
      - name: amount
        tests:
          - not_null        # would have failed on the NULL amounts
```

**b. Reconciliation singular test** (`tests/assert_orders_reconcile_to_payments.sql`):

```sql
-- Returns a row (failing the test) if orders does not add up to the payments it was built from
select
    p.total as payments_total,
    o.total as orders_total
from (select sum(amount) as total from {{ ref('stg_payments') }}) as p
cross join (select sum(amount) as total from {{ ref('orders') }}) as o
where abs(p.total - o.total) > 0.005
```

With the current data this returns 1607 vs 2460, so it fails. It catches the fan-out in A. It doesn't catch NULLs by itself, which is what the `not_null` in (a) is for. The fan-out would also have shown up in `daily_revenue` if it had been compared to `orders` by value rather than by count.

I haven't changed any files. I can apply the `orders.sql` change, the tests, and the `daily_revenue` full refresh if you want, but the feed and ingestion fixes need someone with access to the source.
