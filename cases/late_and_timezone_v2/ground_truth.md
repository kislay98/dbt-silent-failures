# Ground truth: late_and_timezone_v2

**Version:** v2 of `late_and_timezone`, the same injection and key number; the symptom text now carries the first backfilled order's warehouse timestamp (order 100, 2018-01-31 23:18:19 against 2018-02-01 04:48:19 on the store's sync screen), which v1's curated queries showed and the standard sample does not, so the 5h30 offset can be derived from the prompt again. v1's standard runs are kept under `cases/late_and_timezone/runs/standard/` as a note, out of the headline.

**What broke:** `cases/late_and_timezone_v2/inject.py` appends six February orders (ids 100 to 105 for customers 41, 2,
15, 28, 60 and 77, all `completed`, 65.00 in all) to `jaffle_shop/seeds/raw_orders.csv` and their payments (ids 114 to
119) to `jaffle_shop/seeds/raw_payments.csv`, as in `late_orders`; but the store's new sync writes `ordered_at` in UTC,
5h30 earlier than the shop's local time the rest of the feed is in. Order 100, placed at 2018-02-01 04:48:19 on the
store's screen, carries 2018-01-31 23:18:19. Orders 100, 101 and 105 (placed before 05:30 local) land a day early, on
31 January, 2 February and 25 February; the other three keep their date and carry a wrong time of day.

**Where it shows:** `orders`, `customers` and `region_revenue` pick the six up (105 orders, 1737.00), but February gets
32 orders and 474.00 instead of the 33 and 480.00 the store adds up to, and January 30 orders and 502.00 instead of 29
and 496.00. `daily_revenue` is incremental and rebuilds the last three days only, so it keeps 99 orders, 1672.00 and 27
orders, 415.00 for February; six days disagree between the two models (31 January, 2, 8, 13, 22 and 25 February).
Customer 41's first order moves to 31 January (the store says 1 February), customer 2's most recent order to 2 February
(3 February), customer 77's only order to 25 February (26 February).

**Why nothing fails:** every dbt test still passes (44/44): the rows have fresh ids, valid statuses and methods, nothing
tests what a timestamp means, and nothing compares `daily_revenue` to the orders it summarises.

**How the output changes:**

| Measure | Clean | Broken | Correct |
|---|---|---|---|
| `count(*)`, `sum(amount)` from `orders` | 99, 1672.0 | 105, 1737.0 | 105, 1737.0 |
| January, February in `orders` (orders, amount) | 29, 496.0 / 27, 415.0 | 30, 502.0 / 32, 474.0 | 29, 496.0 / 33, 480.0 |
| February in `daily_revenue` | 27, 415.0 | 27, 415.0 | 33, 480.0 |
| days where `orders` and `daily_revenue` disagree | 0 | 6 (31 Jan, 2, 8, 13, 22, 25 Feb) | 0 |
| `stg_orders.ordered_at`, `order_date` for order 100 | absent | 2018-01-31 23:18:19, 2018-01-31 | 2018-02-01 04:48:19, 2018-02-01 |
| `order_date` for orders 101, 105 | absent | 2018-02-02, 2018-02-25 | 2018-02-03, 2018-02-26 |
| `customers.first_order` for customer 41 | 2018-04-07 | 2018-01-31 | 2018-02-01 |
| `customers.most_recent_order` for customer 2 | 2018-01-11 | 2018-02-02 | 2018-02-03 |
| `sum(completed_lifetime_value)` from `customers` | 1103.0 | 1168.0 | 1168.0 |

**Key number:** `select round(sum(amount), 2) from orders where order_date between '2018-02-01' and '2018-02-28'` is 415.0 clean and 474.0 broken (checked by `check_case.py`).

**Correct fix:** both changes, or the output stays wrong. `daily_revenue` needs a full refresh and a strategy that
survives late rows (rebuild every day present in the new source rows, key the window on a load timestamp, or make it a
table); and the six backfilled rows need 5h30 added, exactly those rows, which are the rows the new sync wrote
(`case when order_id >= 100 then ordered_at + interval '5 hours 30 minutes'` in `stg_orders`, or the store's rows
restated at the source). The refresh alone makes the two models agree on a February of 32 orders and 474.00, with the
store's 1 February order counted in January; the shift alone leaves `daily_revenue` at 415.00; shifting every row, or
every row from 1 March, moves orders the shop's own feed wrote in local time. Guards: a per-day reconciliation of
`daily_revenue` to `orders`, and a check that a backfilled order falls in the month the store reports (33 February
orders, none on 31 January).
