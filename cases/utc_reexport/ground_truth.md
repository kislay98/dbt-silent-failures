# Ground truth: utc_reexport

**What broke:** `cases/utc_reexport/inject.py` moves every `ordered_at` in `jaffle_shop/seeds/raw_orders.csv` back by
5 hours 30 minutes, all 99 rows, January to April. It mimics a source that switched its export to UTC and re-exported
the whole history the same way; the shop and its admin screen run on IST (UTC+5:30). There is no cut-over: every row
is shifted.

**Where it shows:** `stg_orders` takes `cast(ordered_at as date)`, so the 14 orders placed before 05:30 local land on
the previous day, spread over the whole history (4 in January, 2 in February, 7 in March, 1 in April): order 73 placed
16 March 01:15 shows 15 March 19:45, order 82 (24 March 05:14) shows 23 March, order 89 (28 March 00:15) shows
27 March. Every timestamp is 5h30 early even where the date holds. Monthly totals are unchanged (no shift crosses a
month boundary), 26 March shows 5 orders instead of 3, 17 January 4 instead of 3. `daily_revenue` only rebuilds 6 to
9 April, so it still holds the old dates for January to March and disagrees with `orders` on 12 days.

**Why nothing fails:** every dbt test still passes (40/40): no test touches dates or times, and the descriptions in
`schema.yml` already say "Date (UTC)".

**How the output changes:**

| Measure | Clean | Broken |
|---|---|---|
| `count(*)` from `orders` where `order_date = '2018-03-26'` | 3 | 5 |
| `count(*)` from `orders` where `order_date = '2018-01-17'` | 3 | 4 |
| orders whose `order_date` moved | 0 | 14 |
| days where `orders` and `daily_revenue` disagree on the count | 0 | 12 |
| `stg_orders.ordered_at` for order 73 | 2018-03-16 01:15:20 | 2018-03-15 19:45:20 |
| `stg_orders.ordered_at` for order 1 | 2018-01-01 17:08:01 | 2018-01-01 11:38:01 |
| orders with a time of day before 05:30 / at or after 18:30 | 14 / 20 | 16 / 14 |
| monthly `count(*)`, `sum(amount)` from `orders` | 29, 27, 35, 8 | unchanged |

**Key number:** `select count(*) from orders where order_date = '2018-03-26'` is 3 clean and 5 broken (checked by `check_case.py`).

**Correct fix:** convert every row to local time before taking the date, `ordered_at + interval 330 minute`
(or `at time zone 'UTC'` then `'Asia/Kolkata'`) in `stg_orders`, or at the source, then full-refresh `daily_revenue`.
The affected values are the dates: `order_date`, `first_order`, `most_recent_order` and the `daily_revenue` days.
A fix is complete when every date matches the clean data; the timestamps may stay in UTC as a uniform, documented
convention (unlike the `utc_switch` cases, where a cut-over fix leaves the column mixed). A fix with a cut-over
(convert only rows after some date or id) leaves the January and February dates wrong. Guards: a reconciliation of
daily order counts to the admin screen, or a test that `order_date` equals the local date of `ordered_at` once the
zone is explicit.
