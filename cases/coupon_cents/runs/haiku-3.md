claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 33 s | USD 0.005 | 2026-10-14 | project bef6b69

## 1. Root cause

**The bad data is in `raw_payments.amount`, not in the dbt code.** The ingestion job is writing some payment amounts in dollars instead of cents. `stg_payments.sql` divides every `amount` by 100, which is correct for cents. A dollar value that gets divided by 100 comes out 100× too small, which is why April's coupon total is 0.33 instead of 33.00.

The coupon figures reconcile exactly if these coupon payments are stored in dollars (I inferred the rows from the sampled orders; the seed data isn't in the prompt, so confirm with the query in section 2):

| order_id | month | stored as | model coupon | correct coupon |
|---|---|---|---|---|
| 76 | Mar | 2 (dollars) | 0.02 | 2.00 |
| 81 | Mar | 2 (dollars) | 0.02 | 2.00 |
| 86 | Mar | 23 (dollars) | 0.23 | 23.00 |
| 92 | Apr | 2 (dollars) | 0.02 | 2.00 |
| 94 | Apr | 7 (dollars) | 0.07 | 7.00 |
| 95 | Apr | 24 (dollars) | 0.24 | 24.00 |

- **March coupon:** the model shows 18.27. Corrected, that is 18.00 (order 58, correctly in cents) + 2 + 2 + 23 = **45.00**, matching the ledger.
- **April coupon:** the model shows 0.33. Corrected, that is 2 + 7 + 24 = **33.00**, matching the ledger.
- **March settlement:** the corrected order amounts add 1.98 + 1.98 + 22.77 = 26.73 to 595.27, giving **622.00**, matching the processor settlement.

Jan and Feb agree because no mis-scaled rows fall in those months. Nothing in the code is wrong for this data. `stg_payments.sql` honours its contract (cents), and the contract was broken for some rows.

**Why the tests passed:** every test is `unique`, `not_null`, or `accepted_values`. None checks units, ranges, or totals against an independent source. `coupon_amount` only has `not_null`, which 0.02 satisfies.

**Blast radius:** the wrong values flow into `orders.amount` and `orders.coupon_amount`, and from there into `customers.customer_lifetime_value`, `customers.completed_lifetime_value`, `region_revenue.revenue`, and `daily_revenue.revenue`.

## 2. Fix

**a) Find the affected rows.** Run this against the warehouse before changing anything:

```sql
select p.id, p.order_id, p.payment_method, p.amount, o.ordered_at
from raw_payments p
join raw_orders o on o.id = p.order_id
where p.payment_method = 'coupon'
  and o.ordered_at >= '2018-03-01' and o.ordered_at < '2018-05-01'
order by p.id;
```

Expect the rows for orders 76, 81, 86, 92, 94, and 95 to show amounts 2, 2, 23, 2, 7, and 24. Check whether any other payment methods in those months are also small. The ingestion job's source for these rows is the real fix point, since the nightly load will otherwise reintroduce the problem.

**b) Correct the raw rows** (snapshot the table first, and confirm with the data owner before writing):

```sql
create table raw_payments_backup as select * from raw_payments;

update raw_payments
set amount = amount * 100
where id in (/* ids from the query above */);
```

**c) Rebuild, including the incremental model.** A normal `dbt build` will not fix `daily_revenue` for March and April. Its incremental filter only rebuilds the last three days, so the old values would remain.

```bash
dbt build --select stg_payments+ --full-refresh -s daily_revenue
```

Or, more simply, `dbt build --full-refresh` for the whole project.

**d) Fix the loader.** The ingestion job should normalise units on write, and it should reject or flag any amount that does not match its source system.

## 3. A test that would have caught this

Reconcile the warehouse against an independent source. The redemption ledger is the right one because marketing already trusts it. Seed it (for example as `raw_coupon_ledger` with `month` and `amount`) and add a singular test:

```sql
-- tests/assert_coupon_revenue_matches_ledger.sql
with warehouse as (
    select date_trunc('month', order_date) as month,
           round(sum(coupon_amount), 2) as coupon_amount
    from {{ ref('orders') }}
    group by 1
),

ledger as (
    select month, round(sum(amount), 2) as ledger_amount
    from {{ ref('raw_coupon_ledger') }}
    group by 1
)

select coalesce(w.month, l.month) as month, w.coupon_amount, l.ledger_amount
from warehouse w
full outer join ledger l on w.month = l.month
where w.coupon_amount is distinct from l.ledger_amount
```

This test fails for March (18.27 vs 45.00) and April (0.33 vs 33.00). A cheaper guard is a range test on the raw data, `tests/assert_payment_amounts_look_like_cents.sql`, which flags coupon rows under 100 cents. It can produce false positives for genuinely small payments, so treat it as a warning.

## Other issues (not the cause of these numbers)

- **`schema.yml` (models):** `customers.total_order_amount` is documented but does not exist. The model produces `customer_lifetime_value`.
- **`region_revenue.sql`:** it joins to `raw_customer_regions`, which has an `updated_at` column, so a customer may appear more than once. If so, payment revenue fans out and is double-counted. There is no `unique` test on `raw_customer_regions.customer_id`. Add one, or dedupe to the latest `updated_at` before joining.
