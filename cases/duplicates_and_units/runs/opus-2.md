claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 45 s | USD 0.175 | 2026-10-14 | project f769fdf+wk6ext | prompt 16fbe10e794d

# Root cause: two data defects in `raw_payments`

`orders.sql` is correct. `orders.amount` sums to 1635.19, which equals `stg_payments` exactly (980 + 185 + 468 + 2.19). So the payments come into the project already wrong. `raw_payments` has two separate problems, and `models/staging/stg_payments.sql` passes both through.

### Defect A: the ingestion job loaded some payments twice, under new IDs

| original | duplicate | order | method | amount |
|---|---|---|---|---|
| 1 | **114** | 1 | credit_card | 10.00 |
| 11 | **115** | 10 | bank_transfer | 26.00 |
| 21 | **116** | 18 | credit_card | 8.00 |
| 71 | **121** | 62 | gift_card | 0.14 (should be 14.00) |

- The duplicates start right after the original 113 payments: ids 114–125, giving 125 rows now.
- Each copy has a new `id`, so `unique_stg_payments_payment_id` still passes.
- This is why order 1 shows 20.00 for a 10.00 charge.
- It accounts for the extra +109.00 on credit card and +57.00 on bank transfer.
- No coupon rows were duplicated, which is why coupons match.

### Defect B: gift card amounts are in dollars, but the code assumes cents

- `stg_payments.sql` divides every `amount` by 100, based on the comment "stored in cents".
- Every gift card row in the output is below $1 (0.03 to 0.30) in every month. So the gift card provider's feed is in whole dollars, and the model shrinks it 100×.
- The numbers confirm this: 2.19 × 100 = 219, minus the duplicated 14 (payment 121) = **205.00**, which matches the provider statement exactly.

### Reconciliation once both are fixed

| method | expected |
|---|---|
| credit card | 980 − 109 = 871 |
| coupon | 185 |
| bank transfer | 468 − 57 = 411 |
| gift card | 205 |
| **total** | **1672.00** ✓ |

This matches the settlement.

# Fix

**Preferred:** fix it at the source.
- Remove the re-delivered rows (ids 114–125) from `raw_payments`.
- Make the ingestion load idempotent.
- Have the gift card feed converted to cents on load, or documented as dollars.

Before deleting anything, confirm that every row in 114–125 really is a copy:

```sql
select d.id, o.id as original_id, d.order_id, d.payment_method, d.amount
from raw_payments d
left join raw_payments o
  on o.order_id = d.order_id and o.payment_method = d.payment_method
 and o.amount = d.amount and o.id < d.id
where d.id > 113
order by d.id;   -- every row should have a non-null original_id
```

**Defensive model fix** in `models/staging/stg_payments.sql`. Use this until the source is cleaned:

```sql
with source as (

    {#-
    Normally we would select from the table here, but we are using seeds to load
    our data in this project
    #}
    select * from {{ ref('raw_payments') }}

),

deduplicated as (

    -- the ingestion job has re-delivered payments under new ids; keep the first copy
    select *
    from source
    qualify row_number() over (
        partition by order_id, payment_method, amount
        order by id
    ) = 1

),

renamed as (

    select
        id as payment_id,
        order_id,
        payment_method,

        -- `amount` is stored in cents, except gift cards, which the provider sends in dollars
        case
            when payment_method = 'gift_card' then cast(amount as double)
            else amount / 100
        end as amount

    from deduplicated

)

select * from renamed
```

**Caveat on the dedup key:** the raw table has no natural idempotency key (no payment timestamp or processor reference). A real split payment of two identical amounts with the same method on the same order would therefore be collapsed. That is why fixing the source is the better long-term option.

After the change:
- Run `dbt build --full-refresh -s daily_revenue+`. `daily_revenue` is incremental and only rebuilds the last 3 days, so without a full refresh it would keep the inflated history.
- Rebuild `orders`, `customers` and `region_revenue` as well.

# Tests that would have caught this

**1. Duplicate payments.** Add this to `models/staging/schema.yml` under `stg_payments`. Put the same test on the `raw_payments` source/seed so it fails before the models run.

```yaml
  - name: stg_payments
    data_tests:
      - dbt_utils.unique_combination_of_columns:
          arguments:
            combination_of_columns: ['order_id', 'payment_method', 'amount']
```

**2. Unit sanity per method.** Save as `tests/assert_payment_amounts_in_dollars.sql`. Any method whose average payment is below $1 is almost certainly in the wrong unit.

```sql
select payment_method, avg(amount) as avg_amount
from {{ ref('stg_payments') }}
where amount > 0
group by payment_method
having avg(amount) < 1
```

**3. Reconciliation against settlements.** This is the test that would catch any future variant of the problem.
- Load the processor and provider statements as a source.
- Save the test below as `tests/assert_orders_reconcile_to_settlement.sql`.

```sql
with ours as (
    select payment_method, round(sum(amount), 2) as total
    from {{ ref('stg_payments') }}
    group by 1
)
select s.payment_method, s.settled_amount, ours.total
from {{ source('finance', 'settlements') }} s
left join ours using (payment_method)
where ours.total is null or abs(ours.total - s.settled_amount) > 0.01
```

**One more gap:** `raw_payment_methods.payment_method` has no `unique` test. A duplicated row there would fan out the join in `orders.sql` and inflate `amount`, not just `fees`. Add `unique` to that column too.
