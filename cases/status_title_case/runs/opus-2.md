claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 18 s | USD 0.107 | 2026-10-14 | project f769fdf | prompt 2b73e24d3b63

## 1. Root cause

**The data:** starting in February 2018, `raw_orders.status` arrives in Title Case (`Completed`, `Placed`, `Shipped`, `Return_pending`). The month/status breakdown shows this: January is all lowercase, February is mixed, and March onward is all capitalized.

**The code that drops it:** `models/customers.sql` compares status case-sensitively:

```sql
sum(case when orders.status = 'completed' then amount else 0 end) as completed_amount
```

So only the 36 lowercase `completed` orders (604.00) are counted. The 31 `Completed` orders (499.00) are silently excluded, and 604 + 499 = **1103**, which is exactly finance's figure. `sum(orders.amount)` is still 1672 because no filter applies there.

**Why no test failed:** the `accepted_values` tests on `status` in both `models/schema.yml` and `models/staging/schema.yml` were widened to accept the capitalized variants. They now allow the new values instead of flagging them. `stg_orders.sql` passes `status` through unnormalized, so every downstream comparison is exposed to the problem.

## 2. Fix

Normalize once in staging, so every consumer sees one canonical value.

`models/staging/stg_orders.sql`:
```sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,
        ordered_at,
        cast(ordered_at as date) as order_date,
        lower(trim(status)) as status

    from source

)
```

Then restore the strict value list in **both** `models/staging/schema.yml` and `models/schema.yml`:
```yaml
      - name: status
        tests:
          - accepted_values:
              arguments:
                values: ['placed', 'shipped', 'completed', 'return_pending', 'returned']
```

After a rebuild, `sum(completed_lifetime_value)` should be 1103.00. The capitalized `Return_pending` and `Placed`/`Shipped` rows also fold into their lowercase buckets, so anything grouping by status gets fixed too.

The upstream feed's casing change should also be reported to whoever owns `raw_orders`. Even so, the staging normalization should stay as a guard.

## 3. Tests that would have caught it

**a) The strict `accepted_values` above.** With the original list, the build would have failed on `Completed` the first day it appeared. The real lesson is to never widen this test just to make a build pass without also normalizing the data.

**b) A reconciliation test** that ties the derived metric back to its source, so a silent filter mismatch can't slip through. Add `tests/assert_completed_ltv_reconciles.sql`:

```sql
-- completed_lifetime_value in customers must equal completed order revenue in orders
with c as (
    select coalesce(sum(completed_lifetime_value), 0) as v from {{ ref('customers') }}
),
o as (
    select coalesce(sum(amount), 0) as v from {{ ref('orders') }}
    where lower(status) = 'completed'
)
select c.v as customers_completed, o.v as orders_completed
from c, o
where abs(c.v - o.v) > 0.01
```

Against the current build this returns one row (604 vs 1103) and fails. Because it uses `lower()` on the orders side, it catches the mismatch even if someone widens `accepted_values` again.
