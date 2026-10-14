# Ground truth: late_orders

**What broke:** `cases/late_orders/inject.py` appends six February orders (ids 100-105, customers 2, 15, 28, 41, 60
and 77, all `completed`, 65.00 in total) to `jaffle_shop/seeds/raw_orders.csv` and their payments (ids 114-119) to
`jaffle_shop/seeds/raw_payments.csv`. It mimics a store whose sync was down for a month and backfilled in the April
load with fresh ids. The rows are legitimate; the failure is that one model never sees them.

**Where it shows:** `stg_orders`, `orders`, `customers` and `region_revenue` read the seeds and pick the six orders up:
`orders` has 105 rows and 1737.00, February has 33 orders and 480.00. `models/daily_revenue.sql` is incremental and on
each run only rebuilds days from `max(order_date) - 3 days`, so it never revisits February: it still has 99 orders,
1672.00 overall and 27 orders, 415.00 for February. Six February days disagree between the two models (3, 8, 13, 17,
22 and 26 February, one order each). Customers 15, 60 and 77 go from no orders to one; customers 2, 28 and 41 from one
to two.

**Why nothing fails:** every dbt test still passes (40/40). The new rows have unique ids, valid statuses and methods,
every order has a payment, and `daily_revenue.order_date` stays unique. Nothing compares the daily table to the
orders it summarises, and the incremental filter is exactly what the model says it does.

**How the output changes:**

| Measure | Clean | Broken |
|---|---|---|
| `count(*)`, `sum(amount)` from `orders` | 99, 1672.0 | 105, 1737.0 |
| `sum(orders)`, `sum(revenue)` from `daily_revenue` | 99, 1672.0 | 99, 1672.0 |
| February: `orders` rows and amount | 27, 415.0 | 33, 480.0 |
| February: `daily_revenue` orders and revenue | 27, 415.0 | 27, 415.0 |
| days where `orders` and `daily_revenue` disagree | 0 | 6 |
| `customers.number_of_orders`, `customer_lifetime_value` for customer 2 | 1, 23.0 | 2, 35.0 |
| `customers.number_of_orders`, `customer_lifetime_value` for customer 60 | NULL, NULL | 1, 20.0 |
| `sum(region_revenue.revenue)` | 1672.0 | 1737.0 |

**Key number:** `select round(sum(amount), 2) from orders where order_date between '2018-02-01' and '2018-02-28'` is 415.0 clean and 480.0 broken (checked by `check_case.py`).

**Correct fix:** the data is right, the model is stale. `dbt build --full-refresh --select daily_revenue` brings in the
six days, and the model needs a strategy that survives late rows: rebuild every day present in the new source rows
(`where order_date in (select distinct order_date from new orders)`), key the incremental filter on a load timestamp
instead of the order date, or widen the lookback and accept the cost. A guard that would have caught it: a singular test
comparing `sum(orders)` and `sum(revenue)` in `daily_revenue` to `orders` per day.
