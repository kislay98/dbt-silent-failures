# Ground truth: rename_and_utc

**What broke:** `cases/rename_and_utc/inject.py` rewrites `jaffle_shop/seeds/raw_orders.csv` the way a new export job
would. It adds `order_ts`, backfilled from `ordered_at` for the 56 orders before 1 March 2018 (shop-local time,
UTC+05:30, as they were). For the 43 orders from 1 March (ids 57 to 99) it writes `order_ts` in UTC, 5h30 earlier,
and leaves `ordered_at` empty. Order 73, placed at 2018-03-16 01:15:20 local, carries 2018-03-15 19:45:20.

**Where it shows:** `stg_orders` still reads `ordered_at`, so `ordered_at` and `order_date` are NULL for the 43
orders: March and April vanish from the monthly report (43 orders, 761.00, under a NULL month), `customers`'
first and most recent order dates ignore them (customer 3's most recent order moves from 11 March to 27 January,
customers 9, 12 and 93 have none). `daily_revenue` keeps its March and April rows from before the load, right by
luck. Behind the rename sits the timezone change: reading `order_ts` as it is puts seven orders a day early (73, 82,
87, 88, 89, 92, 93), because the rows from the cut-over are in UTC while the backfilled rows are local.

**Why nothing fails:** every dbt test still passes (44/44): nothing tests `not_null` on the dates, `unique` on
`order_date` in `daily_revenue` never sees a NULL row, and nothing tests what a timestamp means.

**How the output changes:**

| Measure | Clean | Broken |
|---|---|---|
| `orders` with NULL `order_date` | 0 | 43 (ids 57 to 99, 761.0) |
| `sum(amount)` by month in `orders` | 496.0, 415.0, 622.0, 139.0 | 496.0, 415.0, NULL month 761.0 |
| `sum(revenue)` by month in `daily_revenue` | 496.0, 415.0, 622.0, 139.0 | unchanged (stale) |
| `most_recent_order` for customers 3, 8, 22 | 2018-03-11, 2018-03-12, 2018-03-01 | 2018-01-27, 2018-01-29, 2018-02-17 |
| `first_order` for customers 9, 12, 93 | 2018-03-17, 2018-03-03, 2018-03-01 | NULL |
| `raw_orders` columns | id, user_id, ordered_at, status | plus `order_ts`; `ordered_at` NULL on 43 rows |
| `raw_orders.order_ts` for orders 1 to 56 / 57 to 99 | column absent | equal to `ordered_at` / `ordered_at` minus 5h30 |
| orders whose `order_ts` date differs from the local date | none | 73, 82, 87, 88, 89, 92, 93 |

**Key number:** `select count(*) from orders where order_date is null` is 0 clean and 43 broken (checked by `check_case.py`).

**Correct fix:** both changes, or the output stays wrong: read `order_ts` where `ordered_at` is empty, and add 5h30 to
it on exactly those rows, which are the rows the new job wrote in UTC
(`coalesce(ordered_at, order_ts + interval '5 hours 30 minutes')` in `stg_orders`, or the feed restated at the source).
Every `ordered_at` and `order_date` then matches the clean data. `coalesce(ordered_at, order_ts)` alone brings the
months back with seven orders a day early; adding 5h30 to every `order_ts` moves the 56 backfilled orders instead.
Guards: `not_null` on `stg_orders.ordered_at` or `order_date`, and a reconciliation of daily counts to the admin
(`order_ts` agrees with `ordered_at` wherever both are present, so a test on that pair does not fire).
