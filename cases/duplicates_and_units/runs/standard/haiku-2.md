claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 65 s | USD 0.010 | 2026-10-14 | project ad0b16b | prompt 922df6405b75

## 1. Root cause

The model logic is mostly correct. The bad input is in the payments data, and `stg_payments.sql` passes it through without checking it. I can't query the warehouse from here, so these conclusions are inferred from the numbers. The verification queries below will confirm or rule them out.

**a) Duplicate payment rows in `raw_payments` (credit card and bank transfer overstated).**
- Order 1 shows 20.00 but was charged 10.00, so its payment is counted twice.
- The overstatement is confined to credit card (+109.00) and bank transfer (+57.00). Coupons match, which fits a replay that hit only the card and bank feeds.
- `raw_payments` has 125 rows for 99 orders. Replayed rows would have new `id`s, so `unique_stg_payments_payment_id` still passes.
- `stg_payments.sql` does no deduplication, and `orders.sql` sums every payment row, so the duplicates flow straight into `amount`, `credit_card_amount`, `bank_transfer_amount`, and `fees`.

**b) Gift card amounts in the wrong unit (gift cards understated).**
- `stg_payments.sql` applies `amount / 100` to every row, on the assumption that all of `raw_payments.amount` is cents.
- Gift cards total 2.19 against the provider's 205.00. A single $205 gift card stored as `205` (dollars) would become 2.05 after the division, and 2.05 + 0.14 ≈ 2.19. So a few gift card rows appear to be recorded in dollars.

**What I ruled out:**
- `orders.sql` fan-out: `order_payments` is grouped by `order_id`, and `raw_payment_methods` has one row per method (4 rows), so the join doesn't multiply payments.
- `region_revenue.sql` fan-out: its revenue total (1635.19) equals `orders.amount`, so the region join doesn't multiply rows.
- Coupon logic: coupons match the processor, so the `case` logic is fine.

**Also wrong, separately: `daily_revenue` is stale.** Its revenue sums to 1669.17, not 1635.19. The incremental filter only rebuilds the last three days, so January–March were computed from older data and never refreshed. This means fixing the payments data alone won't correct `daily_revenue`.

### Verify first

```sql
-- a) duplicate payments (expect rows for orders 1 and others)
select order_id, payment_method, amount, count(*) as n, min(id) as first_id, max(id) as dup_id
from raw_payments
group by order_id, payment_method, amount
having count(*) > 1
order by order_id;

-- b) gift card rows, look for small values that look like whole dollars
select id, order_id, amount
from raw_payments
where payment_method = 'gift_card'
order by amount desc;
```

If (a) returns rows whose sums match the 109.00 and 57.00 overstatements, and (b) shows a few gift card amounts that are plausible dollar values, both causes are confirmed.

## 2. Fix

**Staging: deduplicate and keep the cents assumption explicit** (`models/staging/stg_payments.sql`):

```sql
renamed as (

    select
        id as payment_id,
        order_id,
        payment_method,

        -- `amount` is stored in cents, so we convert it to dollars
        amount / 100 as amount

    from source

    -- the ingestion job replays some payments under new ids; keep the first
    qualify row_number() over (partition by order_id, payment_method, amount order by id) = 1

)
```

Caveat: this key is weak. `raw_payments` has no processor transaction reference, so a genuine second payment with the same order, method, and amount would also be dropped. Check with the ingestion owner whether the source has a transaction ID, and if so, partition by that instead.

**Gift cards: fix at the source.** The ingestion job for the gift card feed should send cents, like every other method. Once confirmed, correct the affected rows in `raw_payments` (multiply by 100 for the dollar-denominated ids from check (b)) rather than adding a per-method exception in SQL. If you need an immediate patch in the model, add the conversion only for the specific rows, with a comment that cites the ticket.

**Refresh `daily_revenue` after the data is fixed:**

```bash
dbt build -s daily_revenue --full-refresh
```

Then re-run `dbt build` nightly as normal. The 3-day window will keep recent days current, but a full refresh is needed to correct older days.

## 3. Test that would have caught this

The current tests check only nullity, uniqueness of IDs, and accepted values. None of them check amounts. Add these:

**a) A unit test for the staging logic** (dbt unit tests, `models/staging/schema.yml`):

```yaml
unit_tests:
  - name: stg_payments_drops_replayed_payments
    model: stg_payments
    given:
      - input: ref('raw_payments')
        rows:
          - {id: 1, order_id: 1, payment_method: credit_card, amount: 1000}
          - {id: 114, order_id: 1, payment_method: credit_card, amount: 1000}
          - {id: 2, order_id: 2, payment_method: gift_card, amount: 20500}
    expect:
      rows:
        - {payment_id: 1, order_id: 1, payment_method: credit_card, amount: 10.0}
        - {payment_id: 2, order_id: 2, payment_method: gift_card, amount: 205.0}
```

**b) A reconciliation test against the processor's totals.** This is the test that would have matched finance's numbers directly. Add a seed or source holding each processor's per-method total, then compare:

```sql
-- tests/assert_payment_totals_match_processor.sql
with model_totals as (
    select payment_method, round(sum(amount), 2) as amount
    from {{ ref('stg_payments') }}
    group by payment_method
),
processor_totals as (
    select payment_method, amount from {{ ref('processor_statement_totals') }}
)
select m.payment_method, m.amount as model_amount, p.amount as processor_amount
from model_totals m
join processor_totals p using (payment_method)
where abs(m.amount - p.amount) > 0.005
```

**c) A plausibility check on gift card values**, which would have flagged the unit error. Put it in `schema.yml` under `stg_payments` with a `dbt_utils.expression_is_true` test, or as a singular test:

```sql
-- tests/assert_gift_card_amounts_plausible.sql
select payment_id, amount
from {{ ref('stg_payments') }}
where payment_method = 'gift_card'
  and amount < 1
```

Adjust the threshold to your real gift card range. Its purpose is to flag rows that look like dollars recorded as cents.
