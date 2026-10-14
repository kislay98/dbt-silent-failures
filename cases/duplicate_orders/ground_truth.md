# Ground truth: duplicate_orders

**What broke:** `cases/duplicate_orders/inject.py` appends 16 rows to `jaffle_shop/seeds/raw_orders.csv` and 18 rows to
`jaffle_shop/seeds/raw_payments.csv`: every order placed from 10 to 24 February 2018 (ids 36 to 51) re-sent with a fresh
id (100 to 115), and its payments re-sent with fresh ids (114 to 131) keyed to the new order id. It mimics an extract
that was replayed after a failed load and whose loader assigned new surrogate keys.

**Where it shows:** `orders` has 115 rows instead of 99 and `sum(amount)` is 1913.00 instead of 1672.00 (241.00 of
replayed orders). `customers.number_of_orders` and both lifetime values count the replayed orders (customer 51 goes
from 3 orders and 99.00 to 5 and 140.00), and `region_revenue` counts them in `orders` and `revenue` while
`count(distinct customer_id)` keeps the customer counts right. `daily_revenue` is incremental with a three-day
lookback, so it never picks the rows up: it still says 99 orders and 1672.00 and disagrees with `orders` on 12
February days.

**Why nothing fails:** every dbt test still passes (40/40): `unique` and `not_null` on `order_id` and `payment_id`
pass because the ids are fresh, `relationships` passes because the customers exist, `not_null` on the amount columns
passes because the payments were replayed with the orders, and nothing checks one order per customer and timestamp
or reconciles `orders` to the admin count.

**How the output changes:**

| Measure | Clean | Broken |
|---|---|---|
| `count(*)`, `sum(amount)` from `orders` | 99, 1672.0 | 115, 1913.0 |
| `orders` placed 10 to 24 February: rows, amount | 16, 241.0 | 32, 482.0 |
| `sum(number_of_orders)`, `sum(customer_lifetime_value)` from `customers` | 99, 1672.0 | 115, 1913.0 |
| `customers.number_of_orders`, `customer_lifetime_value` for customer 51 | 3, 99.0 | 5, 140.0 |
| `sum(orders)`, `sum(revenue)` from `region_revenue` | 99, 1672.0 | 115, 1913.0 |
| `sum(orders)`, `sum(revenue)` from `daily_revenue` | 99, 1672.0 | 99, 1672.0 |
| `count(*)` from `raw_orders`, `raw_payments` | 99, 113 | 115, 131 |

**Key number:** `select count(*) from orders` is 99 clean and 115 broken (checked by `check_case.py`).

**Correct fix:** remove the replayed rows (`git checkout jaffle_shop/seeds/raw_orders.csv jaffle_shop/seeds/raw_payments.csv`),
or keep one order per (`customer_id`, `ordered_at`, `status`) in `stg_orders` (lowest id) and drop the payments whose
order was dropped. Guards: a uniqueness test on (`customer_id`, `ordered_at`) in `stg_orders`, or a singular test that
reconciles `orders` to `daily_revenue` by day, or the order count to the admin figure.
