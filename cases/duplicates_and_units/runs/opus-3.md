claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 42 s | USD 0.172 | 2026-10-14 | project f769fdf+wk6ext | prompt 16fbe10e794d

The duplicate rows I can show directly from your sample, and they fully explain the credit card, bank transfer and order 1 gaps. The gift card unit error is inferred from the arithmetic, and it is what makes the numbers reconcile to the cent. One SQL check below confirms both before you change data.

## 1. Root cause

The models aren't the cause. **`raw_payments`** (loaded by the ingestion job, read by `models/staging/stg_payments.sql`) has two data problems:

**a) Duplicate payment rows with new IDs.** Rows from `id` 114 onward re-insert existing payments under fresh IDs. Your `stg_payments` sample shows it:

| dup id | original id | order | method | amount |
|---|---|---|---|---|
| 114 | 1 | 1 | credit_card | 10.00 |
| 115 | 11 | 10 | bank_transfer | 26.00 |
| 116 | 21 | 18 | credit_card | 8.00 |
| 121 | 71 | 62 | gift_card | 0.14 (raw 14) |

- This is why order 1 shows 20.00 when the customer was charged 10.00.
- `unique_stg_payments_payment_id` passes because the duplicates have new IDs.
- jaffle_shop has 113 payments, and you have 125, so there are 12 extra rows (ids 114–125).
- The duplicates add 109.00 to credit cards (980 → 871) and 57.00 to bank transfers (468 → 411). Coupons have no duplicates, which is why they match.

**b) Gift card amounts were loaded in dollars, not cents.** `raw_payments.amount` is `INTEGER` cents, and `stg_payments` divides by 100. The gift card rows hold dollar values, so they come out 100× too small. For example, order 9's gift card is 23 in raw, which should be 2300 cents, and shows as 0.23.

The arithmetic: the raw gift card sum is 219. Dropping the duplicated 14 (id 121) leaves 205, which matches the provider's 205.00 exactly.

With both fixed, the totals reconcile: 871 + 185 + 411 + 205 = **1672.00**, matching the processor settlement.

Confirm before changing anything:

```sql
-- every row here should be an id >= 114 duplicating a lower id
select p.id, p.order_id, p.payment_method, p.amount, min(o.id) as original_id
from raw_payments p
join raw_payments o
  on o.order_id = p.order_id and o.payment_method = p.payment_method
 and o.amount = p.amount and o.id < p.id
group by all order by p.id;

-- expect gift_card to stand out as ~100x smaller than the others
select payment_method, min(amount), avg(amount), max(amount)
from raw_payments where amount > 0 group by 1;
```

## 2. Fix

**Preferred: fix the data at the source** (ingestion job / raw table). Run this after the check above confirms the duplicate list:

```sql
delete from raw_payments where id between 114 and 125;   -- the confirmed duplicates
update raw_payments set amount = amount * 100 where payment_method = 'gift_card';
```

Also fix the ingestion job so it stops re-inserting already-loaded payments and sends gift cards in cents.

**Interim guard in `stg_payments.sql`**, only if the source can't be fixed right away:

```sql
deduped as (

    -- ingestion re-inserted existing payments under new ids
    select *,
        row_number() over (partition by order_id, payment_method, amount order by id) as row_num
    from source

),

renamed as (

    select
        id as payment_id,
        order_id,
        payment_method,

        -- `amount` is stored in cents, except gift cards which the feed sends in dollars
        case when payment_method = 'gift_card' then cast(amount as double) else amount / 100 end as amount

    from deduped
    where row_num = 1

)
```

Caveats on the interim fix:
- It would also collapse two genuinely identical payments on the same order.
- It would double-scale gift cards once ingestion is corrected.
- So remove it when the source is fixed.

**Then rebuild with a full refresh.** `daily_revenue` is incremental and only rebuilds the last 3 days, so a normal run leaves older dates wrong:

```
dbt build --full-refresh
```

`orders`, `customers` (lifetime value), `region_revenue` and `fees` are all affected and get rebuilt too.

## 3. Tests that would have caught it

Add these to `tests/`:

```sql
-- tests/assert_no_duplicate_payments.sql
-- the same payment loaded twice under different ids
select order_id, payment_method, amount, count(*) as n
from {{ ref('stg_payments') }}
group by 1, 2, 3
having count(*) > 1
```

```sql
-- tests/assert_payment_amounts_in_cents.sql
-- raw amounts are cents; a non-zero payment under 1.00 means a feed sent dollars
select *
from {{ ref('raw_payments') }}
where amount > 0 and amount < 100
```

The strongest guard is a reconciliation test. Load the processor and provider settlement totals per method (e.g. a `processor_settlements` seed or source) and fail when `sum(stg_payments.amount)` per method differs from them by more than 0.01. That would have caught both issues, and any future ones, on the first nightly run.
