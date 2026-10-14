claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 44 s | USD 0.175 | 2026-10-14 | project f769fdf+wk6ext | prompt 16fbe10e794d

# Root cause: duplicate payment rows plus gift cards loaded in dollars, all in `raw_payments`

Every model is correct. The bad data is in `raw_payments`, the table the ingestion job loads, and both problems reach the output through `models/staging/stg_payments.sql`. The current tests only check keys and nulls, so neither problem fails them.

### 1. Root cause

**a) Replayed payments under new ids (affects credit card, bank transfer and gift card)**

The ingestion job loaded some payments a second time. Each copy has the same `order_id`, `payment_method` and `amount` as the original but a new `id`:

| order | original | duplicate |
|---|---|---|
| 1 | id 1, credit_card 10.00 | id 114, credit_card 10.00 |
| 10 | id 11, bank_transfer 26.00 | id 115, bank_transfer 26.00 |
| 18 | id 21, credit_card 8.00 | id 116, credit_card 8.00 |
| 62 | id 71, gift_card 0.14 | id 121, gift_card 0.14 |

`unique_stg_payments_payment_id` passes because the ids really are unique.

- There are 125 payments, which matches 113 originals plus the 12 ids 114–125.
- The duplicates add up to 109 for credit card and 57 for bank transfer. That is exactly 980 − 871 and 468 − 411.
- Coupons have no duplicates, which is why coupons match.
- Order 1 shows 20.00 because it has two 10.00 rows.

**b) Gift card amounts are in dollars, not cents**

`stg_payments.sql` divides every `amount` by 100, as the comment "amount is currently stored in cents" says. Gift card rows are already in whole dollars, so they come out 100× too small (0.23, 0.06, 0.03, …).

- Raw gift card total: 219.
- Minus the duplicate 14 from order 62: 205. That is the provider's figure.

**Both fixes together reconcile the total exactly:**

1635.19 − 166.14 (duplicates) − 2.05 + 205.00 (gift card rescale) = **1672.00**

### 2. Fix

**Preferred: fix the data at the source.** Delete the replayed rows from `raw_payments` and fix the ingestion job so it neither re-sends payments nor sends gift cards in dollars. Before deleting, confirm which rows are copies:

```sql
select *
from raw_payments p
where exists (
    select 1 from raw_payments o
    where o.order_id = p.order_id
      and o.payment_method = p.payment_method
      and o.amount = p.amount
      and o.id < p.id
);
-- expect the 12 ids 114–125
```

**Guard in staging, until the feed is fixed.** Update `models/staging/stg_payments.sql`:

```sql
renamed as (

    select
        id as payment_id,
        order_id,
        payment_method,

        -- `amount` is stored in cents, except gift card payments, which the provider feed sends in dollars
        case
            when payment_method = 'gift_card' then amount
            else amount / 100
        end as amount

    from source

    -- the ingestion job re-sent payments under new ids; keep the first copy
    qualify row_number() over (partition by order_id, payment_method, amount order by id) = 1

)
```

Two caveats on this guard:

- The dedupe would also merge two genuine, identical split payments on one order. Removing the rows at the source is safer.
- If the feed is later fixed to send gift cards in cents, remove the `case`.

**Rebuild `daily_revenue` with a full refresh.** It is incremental and only rebuilds the last three days, so a normal run leaves every older date with the wrong revenue:

```
dbt build --full-refresh -s stg_payments+
```

### 3. Tests that would have caught this

`tests/assert_no_replayed_payments.sql` checks the raw table, so it still works after the staging dedupe:

```sql
select order_id, payment_method, amount, count(*) as copies
from {{ ref('raw_payments') }}
group by 1, 2, 3
having count(*) > 1
```

`tests/assert_payment_amounts_in_dollars.sql` flags any non-zero payment under $1, which usually means cents were divided twice or dollars were divided once:

```sql
select *
from {{ ref('stg_payments') }}
where amount > 0 and amount < 1
```

The strongest check would compare totals per method and month against the processor and provider settlement files, if those can be loaded as a source. That is exactly the comparison finance made by hand.

### Other issues to check (not the cause here)

- **`region_revenue` can double count.** `raw_customer_regions` has an `updated_at` column, which suggests it keeps history. If a customer has more than one row, revenue is counted once per row. Add a `unique` test on `customer_id`, or select only the current row.
- **Doc/column mismatch.** `models/schema.yml` documents `customers.total_order_amount`, but the model outputs `customer_lifetime_value`.
