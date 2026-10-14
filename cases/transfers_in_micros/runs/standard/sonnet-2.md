claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 30 s | USD 0.099 | 2026-10-15 | project ad0b16b | prompt 75e42f540dd8

## 1. Root cause

**File: `seeds/raw_payments.csv`** (the `raw_payments` table, which `stg_payments.sql` reads). I can't see the seed, so the details below are inferred from the output.

From about mid-March onward, the `bank_transfer` rows carry amounts in **micro-dollars (×1,000,000)** instead of cents (×100). That is 10,000× too large. The evidence:

- Order 73 shows 30,000.00 and the customer paid 3.00. Raw `amount` is 3,000,000, which is 3.00 in micro-dollars, but `stg_payments` divides by 100 and gets 30,000.
- Raw rows like `order 92 = 15,000,000` and `order 97 = 14,000,000` decode to 15.00 and 14.00.
- Order 78 shows 260,000.00, which is 26.00 in micro-dollars.
- January, February and early March bank transfers are 2–2,000 raw (cents) and are fine. Only the `bank_transfer` method is affected. `credit_card`, `coupon` and `gift_card` reconcile.

`stg_payments.sql` hard-codes `amount / 100`, so it silently inflates those rows. Everything downstream (`orders`, `fees`, `customers.customer_lifetime_value`, `region_revenue`, `daily_revenue`) inherits it.

All 35 checks passed because none of them look at magnitudes or reconcile to an external total. They only check not-null, unique, accepted values for the method and status, and the FK relationship. Corrupted amounts satisfy all of those.

**Secondary issue:** `daily_revenue` is stale. It shows March = 622 while `orders` shows 1,140,508 for the same month. The incremental model only rebuilds the last 3 days, so earlier history was never recomputed. Fixing the upstream data won't fix it without a full refresh.

## 2. Fix

**Fix the data first.** Confirm the cutoff:

```sql
select p.id, p.order_id, o.ordered_at, p.amount
from raw_payments p join raw_orders o on o.id = p.order_id
where p.payment_method = 'bank_transfer'
order by o.ordered_at;
```

Every row from the first order with a micro-dollar value onward should be divided by 10,000. Either correct them in the source or, better, have the ingestion job normalise the unit. I'd ask whoever owns the feed what changed in mid-March, because new rows will keep arriving in the wrong unit.

**Stopgap in `stg_payments.sql`**, with the cutoff taken from the query above:

```sql
renamed as (

    select
        payments.id as payment_id,
        payments.order_id,
        payments.payment_method,

        -- `amount` is stored in cents, so we convert it to dollars.
        -- bank_transfer feed switched to micro-dollars for orders on/after <CUTOFF>
        case
            when payments.payment_method = 'bank_transfer'
                 and orders.ordered_at >= timestamp '<CUTOFF>'
            then payments.amount / 1000000
            else payments.amount / 100
        end as amount

    from source as payments
    left join {{ ref('raw_orders') }} as orders
        on payments.order_id = orders.id

)
```

Then run `dbt build --full-refresh -s daily_revenue+ orders+` (or at least `--full-refresh` on `daily_revenue`). Expected results afterwards: March bank transfer = 162.00 and April = 39.00.

## 3. Tests that would have caught it

**a) Range check on staging amounts** (needs `dbt_utils`; set the max just above your largest legitimate payment):

```yaml
# models/staging/schema.yml, under stg_payments
      - name: amount
        tests:
          - dbt_utils.accepted_range:
              arguments:
                min_value: 0
                max_value: 1000
```

This would have failed on the first inflated row.

**b) Reconcile to the processor statement.** Load the statement totals as a seed, `seeds/processor_statement.csv` with `month, payment_method, amount`, then add `tests/assert_orders_match_processor_statement.sql`:

```sql
with ours as (
    select date_trunc('month', order_date) as month, 'bank_transfer' as payment_method,
           sum(bank_transfer_amount) as amount
    from {{ ref('orders') }}
    group by 1
)
select s.month, s.payment_method, s.amount as statement, o.amount as ours
from {{ ref('processor_statement') }} s
left join ours o using (month, payment_method)
where abs(coalesce(o.amount, 0) - s.amount) > 0.01
```

**c) Catch the stale incremental model** with `tests/assert_daily_revenue_matches_orders.sql`:

```sql
select d.order_date, d.revenue, o.revenue as expected
from {{ ref('daily_revenue') }} d
join (select order_date, sum(amount) as revenue from {{ ref('orders') }} group by 1) o
  using (order_date)
where abs(d.revenue - o.revenue) > 0.01
```

Test (c) would have flagged the March divergence of 622 vs 1,140,508 immediately.
