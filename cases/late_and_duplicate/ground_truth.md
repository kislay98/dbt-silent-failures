# Ground truth: late_and_duplicate

**What broke:** `cases/late_and_duplicate/inject.py` does two things to the seeds in one load. It appends the six
February orders of `late_orders` (ids 100-105 with payments 114-119, 65.00, all legitimate), and it replays every 10th
payment under a fresh id as in `duplicate_payments` (ids 120-131, copies of 1, 11, ..., 111, for orders 1, 10, 18, 26,
36, 46, 54, 62, 71, 79, 88 and 97; 180.00). Types 6 and 1 together: late-arriving data plus duplicate source rows.

**Where it shows:** `orders` has 105 rows and 1917.00: the true 1737.00 plus 180.00 of duplicates. The 12 affected
orders double (order 1: 10.00 to 20.00, order 10: 26.00 to 52.00) and so do their customers' lifetime values.
`daily_revenue` is incremental with a three-day lookback: it never sees the six February days (still 27 orders and
415.00 for February) but it does rebuild 6 to 9 April, so the replayed payment of order 97 (7 April) gets in: 99
orders and 1686.00. Sixteen days disagree between the two models, for two different reasons: four January, two
March and some February days carry duplicates, and six February days are missing late orders. `region_revenue` and
`stg_payments` carry both effects (131 payments for 105 orders, 1917.00).

**Why nothing fails:** every dbt test still passes (40/40). The late rows have fresh unique ids and valid values; the
replayed payments have fresh ids too, so `unique` on `payment_id` holds; nothing tests one payment per order and
method, and nothing reconciles `daily_revenue` to `orders` or either to the settlement.

**How the output changes:**

| Measure | Clean | Broken |
|---|---|---|
| `count(*)`, `sum(amount)` from `orders` | 99, 1672.0 | 105, 1917.0 |
| `sum(orders)`, `sum(revenue)` from `daily_revenue` | 99, 1672.0 | 99, 1686.0 |
| February: `orders` rows and amount | 27, 415.0 | 33, 515.0 |
| February: `daily_revenue` orders and revenue | 27, 415.0 | 27, 415.0 |
| `orders.amount` for orders 1 and 10 | 10.0, 26.0 | 20.0, 52.0 |
| `count(*)`, `count(distinct order_id)`, `sum(amount)` from `stg_payments` | 113, 99, 1672.0 | 131, 105, 1917.0 |
| days where `orders` and `daily_revenue` disagree | 0 | 16 |
| orders per month (Jan, Feb, Mar, Apr) amount | 496, 415, 622, 139 | 570, 515, 679, 153 |

**Key number:** `select round(sum(amount), 2) from orders` is 1672.0 clean and 1917.0 broken (checked by `check_case.py`).

**Correct fix:** remove the 12 replayed payments (ids 120-131) and keep the six late orders, then full-refresh
`daily_revenue`: `orders` and `daily_revenue` both come to 1737.00, matching the settlement. Restoring both seeds
undoes the legitimate backfill as well, so it is not the right fix. Guards: a uniqueness test on
(`order_id`, `payment_method`, `amount`) in `stg_payments`, a reconciliation of `daily_revenue` to `orders` per day,
and a reconciliation of `orders` to the settlement.
