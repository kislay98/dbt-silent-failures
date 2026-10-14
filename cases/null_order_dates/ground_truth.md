# Ground truth: null_order_dates

**What broke:** `cases/null_order_dates/inject.py` blanks `ordered_at` in `jaffle_shop/seeds/raw_orders.csv` for the 8
orders placed from 10 to 16 March 2018 (ids 66 to 73, 149.00). It mimics an export whose timestamp column failed to
parse for one batch; the rows load with a NULL timestamp.

**Where it shows:** `stg_orders` casts the NULL to a NULL `order_date`, so `orders` has 8 rows with no date and March
by order date shrinks from 35 orders and 622.00 to 27 and 473.00, with a NULL bucket of 8 and 149.00. In `customers`,
`min` and `max` skip NULL, so customers 19 and 36 (one order each) lose `first_order` and `most_recent_order`, and
customers 3, 8, 30, 42, 53 and 79 fall back to an earlier date. `daily_revenue` is unchanged (March 622.00, total
1672.00): its incremental filter compares the date to the lookback window and drops a NULL, and delete+insert only
touches the dates in the new batch, so the dashboard keeps the March it built before the batch and is right by luck.

**Why nothing fails:** every dbt test still passes (40/40): nothing tests `not_null` on `ordered_at` or
`order_date` in staging or in `orders`, and the NULL rows never reach `daily_revenue`, whose `not_null` on `order_date`
would fire on a full refresh.

**How the output changes:**

| Measure | Clean | Broken |
|---|---|---|
| `count(*)` from `orders` where `order_date is null` | 0 | 8 |
| March `count(*)`, `sum(amount)` from `orders` by `order_date` | 35, 622.0 | 27, 473.0 |
| March `sum(orders)`, `sum(revenue)` from `daily_revenue` | 35, 622.0 | 35, 622.0 |
| `customers.first_order`, `most_recent_order` for customer 36 | 2018-03-10, 2018-03-10 | NULL, NULL |
| `customers.most_recent_order` for customer 3 | 2018-03-11 | 2018-01-27 |
| `sum(revenue)` from `daily_revenue` | 1672.0 | 1672.0 |
| `raw_orders.ordered_at` for ids 66 to 73 | timestamps | NULL |

**Key number:** `select round(sum(amount), 2) from orders where order_date between '2018-03-01' and '2018-03-31'` is 622.0 clean and 473.0 broken (checked by `check_case.py`).

**Correct fix:** the timestamps are gone from the feed, so reload the batch from the source with its timestamps
(`git checkout jaffle_shop/seeds/raw_orders.csv`); nothing in the warehouse can assign the 8 orders to their days.
Dropping or defaulting the NULL dates hides the loss. Guards: `not_null` on `stg_orders.ordered_at` or `order_date`,
or a singular test that reconciles `orders` to `daily_revenue` by day.
