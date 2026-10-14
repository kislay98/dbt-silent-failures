claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 78 s | USD 0.012 | 2026-10-14 | project ad0b16b | prompt eb0b3a80ab67

## 1. Root cause: not settled from these files

The gap is 136.00 in both places: March (279 vs 143) and since January (1103 vs 967). Everything outside March agrees, so the admin counts 136 more completed orders in March than the warehouse holds. The dbt logic is not the source of that difference.

**Ruled out by the pasted evidence:**

- **Status values.** The `accepted_values` tests on `stg_orders` and `orders` passed, so there are no case or whitespace variants of `completed`.
- **Row fan-out.** `orders` has 99 rows, matching `raw_orders`. `unique` on `order_id` passed. `customers` (967) and the direct status query (967) agree.
- **Customer joins.** `relationships` passed, and `region_revenue` covers all 99 orders and 1612.60 of amount without duplication.
- **Payment method join.** `raw_payment_methods` has 4 rows, one per method, so the `orders.sql` join does not multiply amounts.
- **Timezone.** A UTC-versus-AEST boundary shift moves orders between months but cannot change the since-January total, and the since-January gap is also 136.

**Likely location:** the 136 is either orders the admin has that the ingestion load lacks or doesn't reflect, or a status the admin has changed since the load. Both would live in the ingestion job, which is outside this project. `raw_orders` has no load timestamp, so there is no way to see how stale it is.

**Query to settle it.** Export the admin's completed March orders (id, amount) and compare them against the warehouse:

```sql
-- warehouse side
select o.id, o.status, o.ordered_at, sum(p.amount) / 100.0 as paid
from raw_orders o
left join raw_payments p on p.order_id = o.id
where o.ordered_at >= '2018-03-01' and o.ordered_at < '2018-04-01'
group by 1, 2, 3
order by 1;
```

Then check which admin-completed ids are missing from `raw_orders`, or present with a different status.

**Real defects I did find (separate from the 136):**

- **Four seed tests never ran.** The build reports "Found 32 data tests" but runs only 28. The missing four are the `not_null` tests on `raw_customer_regions` and `raw_payment_methods`, because seeds are absent from the log. The build still reports `PASS=35 ERROR=0`, which hides this.
- **`daily_revenue` is stale.** It is overstated versus `orders`: March 622.00 vs 595.27 (+26.73), April 139.00 vs 106.33 (+32.67), 59.40 in total. Only the last three days are rebuilt, so older days never pick up later payment changes.
- **No uniqueness on the lookup seeds.** `raw_payment_methods.payment_method` and `raw_customer_regions.customer_id` have no `unique` test. A duplicate in either would double `orders.amount` or the regional revenue.

## 2. Fix

For the 136 itself, the fix goes in the ingestion job once the query above identifies the rows. I would not change the dbt models for it; `customers.sql` and `orders.sql` compute completed revenue correctly from what they receive.

For the defects above:

- Run the seeds in the nightly build, or stop excluding them, so the four seed tests execute.
- Add `unique` to `raw_payment_methods.payment_method` and `raw_customer_regions.customer_id` in `models/schema.yml`.
- Rebuild `daily_revenue` with `dbt run -s daily_revenue --full-refresh`, and widen the incremental window or schedule a periodic full refresh.

## 3. Test that would have caught this

The existing tests check shape (not null, unique, accepted values) but never check totals. A singular test that reconciles completed revenue to an independent source would have failed on the March gap. Load finance's monthly ledger totals as a seed (`raw_finance_monthly_revenue`) and add `tests/assert_completed_revenue_matches_ledger.sql`:

```sql
with model as (
    select
        date_trunc('month', order_date) as month,
        sum(amount) as model_amount
    from {{ ref('orders') }}
    where status = 'completed'
    group by 1
),

ledger as (
    select month, amount as ledger_amount
    from {{ ref('raw_finance_monthly_revenue') }}
)

select
    coalesce(model.month, ledger.month) as month,
    model.model_amount,
    ledger.ledger_amount
from model
full outer join ledger on model.month = ledger.month
where abs(coalesce(model.model_amount, 0) - coalesce(ledger.ledger_amount, 0)) > 0.005
```

This fails on any month where the warehouse and the ledger disagree, which is the failure finance reported. Nothing in this project has been modified. The SQL above is untested, since I could not run it against the warehouse.
