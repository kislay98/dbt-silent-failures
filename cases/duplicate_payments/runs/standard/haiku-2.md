claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 47 s | USD 0.010 | 2026-10-14 | project ad0b16b | prompt b041d9ee24db

## 1. Root cause

The duplicates are in the seed `jaffle_shop/seeds/raw_payments.csv`, rows with `id` 114 through 125. Each one repeats an earlier payment with the same `order_id`, `payment_method`, and `amount`, under a new `id`:

| Duplicate row | Copies row | Order | Method | Amount |
|---|---|---|---|---|
| 114 | 1 | 1 | credit_card | 1000 |
| 115 | 11 | 10 | bank_transfer | 2600 |
| 116 | 21 | 18 | credit_card | 800 |
| 117 | 31 | 26 | credit_card | 3000 |
| 118 | 41 | 36 | bank_transfer | 900 |
| 119 | 51 | 46 | bank_transfer | 800 |
| 120 | 61 | 54 | credit_card | 1800 |
| 121 | 71 | 62 | gift_card | 1400 |
| 122 | 81 | 71 | credit_card | 500 |
| 123 | 91 | 79 | credit_card | 900 |
| 124 | 101 | 88 | credit_card | 2900 |
| 125 | 111 | 97 | bank_transfer | 1400 |

The twelve rows total 18,000 cents, which is 180.00. That is exactly the gap between the two numbers: 1852.00 − 1672.00 = 180.00. Order 1 is the example you gave: rows 1 and 114 both charge 10.00, so `orders.amount` is 20.00.

Nothing in the pipeline removes them:
- `stg_payments` passes every row through. Its `payment_id` is unique because the duplicates get new ids, so the `unique` and `not_null` tests pass.
- `orders.sql` sums `payments.amount` per order, so the duplicates are counted.
- No test checks the total against an external control.

The same inflation carries into:
- `orders.amount` and `orders.fees` (fees are overstated for the affected orders).
- `customers.customer_lifetime_value` and `completed_lifetime_value`. Both sum to 1852 and 1208 in the output.
- `region_revenue.revenue`, which sums to 1852.

**`daily_revenue` is also stale, a second and separate problem.** Its monthly totals are 496 / 415 / 622 / 153, which sum to 1686, not 1852. The gaps are Jan 74, Feb 35, Mar 57, and they match the duplicates' order dates exactly. April matches because it falls inside the 3-day incremental rebuild window. The incremental filter only refreshes recent dates, so the historical rows were built before the duplicates existed and will never correct themselves.

I reasoned from the files and output you pasted and did not run dbt. The figures reconcile exactly, which I take as strong evidence.

## 2. Fix

**Data change.** Delete rows 114–125 from `jaffle_shop/seeds/raw_payments.csv`. Before you commit the deletion, confirm against the processor's settlement file that these twelve are not real charges. A 1672.00 settlement total would confirm it.

**Rebuild with a full refresh.** The 3-day window will not repair Jan–Mar in `daily_revenue`:

```
dbt build --full-refresh
```

**Optional guard.** If you want the pipeline to reject duplicates rather than rely on people checking the seed, add a check upstream instead of silently deduplicating in `stg_payments`. Silent deduplication could hide genuine repeat payments. If the processor provides a transaction id, use that as the dedup key. The seed has none, so the test in part 3 is the safer guard.

**Follow-up.** Consider making `daily_revenue` a table, or running a periodic `--full-refresh`, since the 3-day window cannot fix historical corrections.

## 3. Test that would have caught it

Add a singular test that fails on any duplicate payment on the natural key. None of the original 113 rows share an `(order_id, payment_method, amount)` triple, so the test passes once the rows are removed.

`jaffle_shop/tests/assert_no_duplicate_payments.sql`:

```sql
select order_id, payment_method, amount, count(*) as n
from {{ ref('stg_payments') }}
group by 1, 2, 3
having count(*) > 1
```

Add a reconciliation test that checks `orders` against the payments feed. It catches this bug even if a future duplicate has a different key.

`jaffle_shop/tests/assert_orders_reconcile_to_payments.sql`:

```sql
with payments_total as (
    select sum(amount) as total from {{ ref('stg_payments') }}
),

orders_total as (
    select sum(amount) as total from {{ ref('orders') }}
)

select *
from payments_total
cross join orders_total
where abs(payments_total.total - orders_total.total) > 0.005
```

For the strongest guard, compare the payments total against the processor's settlement total in a seed or source, not only against `orders`. Comparing `orders` to `stg_payments` only checks internal consistency. If both sides read the same duplicated rows, the test still passes.

## Other things I noticed

- `customers` documents a `total_order_amount` column that does not exist. The column is `customer_lifetime_value`.
- The `orders.amount` description says "Total amount (AUD) of the order", but it is actually the sum of payments. It should say that explicitly.
