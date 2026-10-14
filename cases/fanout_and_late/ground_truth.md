# Ground truth: fanout_and_late

**What broke:** `cases/fanout_and_late/inject.py` appends six February orders (ids 100 to 105 for customers 2, 15, 28,
41, 60 and 77, all `completed`, 65.00 in all) to `jaffle_shop/seeds/raw_orders.csv` and their payments (ids 114 to 119)
to `jaffle_shop/seeds/raw_payments.csv`, as in `late_orders`; and it appends the region rows of those six customers to
`jaffle_shop/seeds/raw_customer_regions.csv` again, same region, `updated_at` 2018-04-02, as in `region_fanout`: the
store's sync re-sent its customer records with the backfill and the loader appended them.

**Where it shows:** `orders`, `customers` and `region_revenue` pick the six orders up (105 orders, 1737.00, February
33 orders and 480.00). `daily_revenue` is incremental and rebuilds the last three days only, so it keeps 99 orders,
1672.00 and 27 orders, 415.00 for February; six days disagree between the two models (3, 8, 13, 17, 22 and 26
February). `region_revenue` joins the regions feed before it aggregates, so every payment of the six customers is
summed twice: 1838.00 instead of 1737.00 (NSW 394.00 instead of 349.00, QLD 377.00 instead of 356.00, VIC 446.00
instead of 411.00), while the distinct customer and order counts per region are right. The two defects pull the same
way: the regional report is 101.00 over `orders`, and `orders` is 65.00 over the dashboard.

**Why nothing fails:** every dbt test still passes (44/44): the orders and payments have fresh ids, the regions seed is
tested `not_null` and not `unique`, `region_revenue.region` stays unique after the `group by`, and nothing compares
`daily_revenue` or `region_revenue` to `orders`.

**How the output changes:**

| Measure | Clean | Broken | Correct |
|---|---|---|---|
| `count(*)`, `sum(amount)` from `orders` | 99, 1672.0 | 105, 1737.0 | 105, 1737.0 |
| February in `orders` / in `daily_revenue` | 27, 415.0 / 27, 415.0 | 33, 480.0 / 27, 415.0 | 33, 480.0 / 33, 480.0 |
| `sum(revenue)` from `region_revenue` | 1672.0 | 1838.0 | 1737.0 |
| `region_revenue.revenue` for NSW, QLD, VIC | 314.0, 338.0, 399.0 | 394.0, 377.0, 446.0 | 349.0, 356.0, 411.0 |
| `region_revenue.customers`, `orders` for NSW | 12, 20 | 14, 23 | 14, 23 |
| days where `orders` and `daily_revenue` disagree | 0 | 6 | 0 |
| `customers.number_of_orders`, `customer_lifetime_value` for customer 2 | 1, 23.0 | 2, 35.0 | 2, 35.0 |
| `count(*)`, `count(distinct customer_id)` from `raw_customer_regions` | 100, 100 | 106, 100 | 100, 100 (or one row read per customer) |

**Key number:** `select round((select sum(revenue) from region_revenue) - (select sum(amount) from orders), 2)` is 0.0 clean and 101.0 broken (checked by `check_case.py`).

**Correct fix:** both changes, or the output stays wrong. `daily_revenue` needs a full refresh and a strategy that
survives late rows (rebuild every day present in the new source rows, key the window on a load timestamp, or make it a
table); and `region_revenue` needs one region row per customer (the latest `updated_at`, or distinct customer and
region, or the loader upserting on the key). The refresh alone leaves the regional report at 1838.00; one row per
customer alone leaves the dashboard at 1672.00; restoring the seeds removes six real orders. Guards: a per-day
reconciliation of `daily_revenue` to `orders`, `unique` on `raw_customer_regions.customer_id` (or a test that
`sum(region_revenue.revenue)` equals `sum(orders.amount)`).
