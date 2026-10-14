# Ground truth: utc_switch_blind

**What broke:** `cases/utc_switch_blind/inject.py` (the same injection as `utc_switch`) rewrites `ordered_at` in `jaffle_shop/seeds/raw_orders.csv` for the 43
orders placed on or after 2018-03-01 (ids 57-99), subtracting 5h30. It mimics an orders feed that switched from the
shop's local time (UTC+05:30) to UTC at a release without telling anyone. Timestamps before the cut-over are untouched.

**Where it shows:** `models/staging/stg_orders.sql` derives `order_date` with `cast(ordered_at as date)`, so every order
placed between 00:00 and 05:29 local time from March on lands on the previous day. Seven orders move: 73 (16 to 15
March), 82 (24 to 23 March), 87 and 88 (27 to 26 March), 89 (28 to 27 March), 92 (2 to 1 April), 93 (3 to 2 April).
`orders.order_date` moves with them, and `customers.first_order` or `most_recent_order` moves for customers 19, 21,
46, 66, 84 and 91. Amounts never change, no order crosses a month boundary, and `daily_revenue` keeps the old
assignment because its three-day lookback does not reach the moved days (it would move them on a full refresh). The
other 36 shifted orders keep their date but carry a wrong time of day.

**What makes it harder:** the prompt names no example order and no offset. It gives the shop's country, the
admin screen's daily counts from 1 March, the warehouse's daily counts, and the timestamps of the orders between 23 and
28 March. The moved orders bound the offset from below (order 89 at 18:45:59 is counted on the next day, so the shift is
at least 5h14m) and the unmoved ones from above, which with India gives 5h30.

**Why nothing fails:** every dbt test still passes (40/40). No test can check what a timestamp means; the values are
valid timestamps, unique ids and accepted statuses are untouched, and `daily_revenue.order_date` stays unique.

**How the output changes:**

| Measure | Clean | Broken |
|---|---|---|
| `count(*) from orders` on 26, 27, 28 March | 3, 2, 1 | 5, 1, 0 |
| `count(*) from orders` on 1, 2, 3 April | 0, 1, 2 | 1, 1, 1 |
| `stg_orders.ordered_at`, `order_date` for order 73 | 2018-03-16 01:15:20, 2018-03-16 | 2018-03-15 19:45:20, 2018-03-15 |
| `stg_orders.ordered_at` for order 57 (date unchanged) | 2018-03-01 12:18:40 | 2018-03-01 06:48:40 |
| `customers.most_recent_order` for customer 66 | 2018-04-03 | 2018-04-02 |
| `customers.first_order` for customer 19 | 2018-03-16 | 2018-03-15 |
| orders per month (Jan, Feb, Mar, Apr) | 29, 27, 35, 8 | 29, 27, 35, 8 |
| average hour of day of `ordered_at`, orders from 1 March | 12.7 | 11.1 |
| `sum(orders.amount)` | 1672.0 | 1672.0 |

**Key number:** `select count(*) from orders where order_date = '2018-03-26'` is 3 clean and 5 broken (checked by `check_case.py`).

**Correct fix:** restore the feed (`git checkout jaffle_shop/seeds/raw_orders.csv`), or convert the shifted rows back
to local time before deriving the date in `stg_orders`: `ordered_at + interval 5 hour + interval 30 minute` for rows
from 2018-03-01, or convert every row to the shop's zone explicitly once the feed is known to be UTC. A guard that
would have caught it: a singular test comparing daily order counts to the app's count for the previous day, or a test
on the time-of-day distribution (share of orders between 00:00 and 06:00) per week.
