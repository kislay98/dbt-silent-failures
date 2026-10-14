claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 19 s | USD 0.004 | 2026-10-14

## 1. Root cause

**File:** `jaffle_shop/models/region_revenue.sql` (the `regions` CTE and the `final` join), with the input data in the `raw_customer_regions` seed.

The seed has 108 rows (`dbt build` log: `1 of 40 OK loaded seed file main.raw_customer_regions [INSERT 108]`), but there are only 100 customers. The seed has a `customer_id` `not_null` test but no `unique` test, so the duplicate rows pass. The `updated_at` column suggests the feed keeps several snapshots per customer.

The model joins each customer to every matching region row, so a customer with two region rows has each of their payments counted twice:

- `revenue` is `sum(payments.amount)` after the join, so it is inflated by every duplicated payment.
- `customers` and `orders` use `count(distinct ...)`, which is immune to fan-out. That is why those counts look right while revenue does not.

The overstatement is 1846 − 1672 = 174, which matches the payments of the roughly 8 duplicated customers. This is inferred from the output, since I can't see the seed rows. Confirm it with:

```sql
select customer_id, count(*) as n
from raw_customer_regions
group by customer_id
having count(*) > 1;
```

Also check whether the duplicate rows disagree on `region`. If they do, those customers are also being counted in two regions, which would inflate the customer and order counts too. You said those look right, so the duplicates are probably the same region, but check.

The tests didn't catch it because the only checks on this model are `unique` and `not_null` on `region` and `revenue`. None of them compares the output to a source total.

## 2. Fix

Keep one region row per customer, the most recent one, before the join. Change the `regions` CTE in `region_revenue.sql`:

```sql
regions as (

    select customer_id, region

    from {{ ref('raw_customer_regions') }}

    qualify row_number() over (partition by customer_id order by updated_at desc) = 1

),
```

`QUALIFY` is supported by DuckDB. If `updated_at` can tie for a customer, add a tiebreaker column to the `order by`.

Before relying on "latest wins", confirm with finance that `updated_at` is the right rule for region assignment. The reconciliation to settlement only confirms the revenue total, not which region each customer belongs to.

After the change, `sum(revenue)` should be 1672.00 and `sum(customers)` should still be 62 (or fewer, if a customer moved regions).

## 3. Test that would have caught it

Add a reconciliation singular test. It checks the invariant finance actually cares about: region revenue must add up to order revenue. Create `jaffle_shop/tests/region_revenue_reconciles_to_orders.sql`:

```sql
with totals as (

    select
        (select sum(revenue) from {{ ref('region_revenue') }}) as region_total,
        (select sum(amount) from {{ ref('orders') }}) as orders_total

)

select *
from totals
where abs(region_total - orders_total) > 0.005
```

A singular test fails when it returns rows, so this fails whenever the totals differ.

Also add a uniqueness test on the seed key, in `jaffle_shop/models/schema.yml`:

```yaml
seeds:
  - name: raw_customer_regions
    columns:
      - name: customer_id
        tests:
          - not_null
          - unique
```

The `unique` test will fail on the current seed until the duplicates are resolved or the model dedupes them. That failure is correct: it flags the bad input at the source, instead of letting the model silently absorb it. If you'd rather keep the seed as-is, you can make this test a `warn` instead.

I haven't run any of this. The root cause and the 174 figure are inferred from the log, the row counts, and the sample output, and the verification query above should confirm them.
