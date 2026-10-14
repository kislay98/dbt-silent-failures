# Ground truth: regions_fanout_history

**What broke:** `cases/regions_fanout_history/inject.py` appends 6 rows to `jaffle_shop/seeds/raw_customer_regions.csv`:
the six customers with the most orders move region on 1 March 2018 and the feed adds a second row each with the new
region and `updated_at` 2018-03-01 instead of updating the old one: customer 3 NSW to QLD, 22 VIC to WA, 51 VIC to WA,
54 QLD to SA, 66 NSW to QLD, 71 WA to ACT. The feed now carries history; the project assumes one row per customer.

**Where it shows:** `models/region_revenue.sql` joins the feed before it aggregates, so each moved customer, their
orders and their payments are counted in both their old and their new region: `sum(customers)` goes from 62 to 68,
`sum(orders)` from 99 to 119 and `sum(revenue)` from 1672.00 to 2028.00. ACT, QLD, SA and WA gain the moved
customers while NSW, VIC and TAS keep them. Finance reports revenue by the customer's current region, so the correct
output is the latest row per customer: ACT 5 / 8 / 119.00, NSW 10 / 14 / 210.00, QLD 11 / 21 / 385.00,
SA 5 / 11 / 162.00, TAS 7 / 9 / 130.00, VIC 10 / 15 / 248.00, WA 14 / 21 / 418.00, totals 62 / 99 / 1672.00. That
differs from the clean table (the customers did move), so a fix that keeps the oldest row reproduces the clean numbers
and is wrong, and an as-of join on order date is a different report (region at order time).

**Why nothing fails:** every dbt test still passes (40/40): the seed is tested `not_null`, not `unique`;
`region_revenue.region` stays unique; nothing reconciles the region report to `orders` or the customer counts to the
customer base.

**How the output changes:**

| Measure | Clean | Broken | Correct (latest region) |
|---|---|---|---|
| `sum(customers)`, `sum(orders)`, `sum(revenue)` from `region_revenue` | 62, 99, 1672.0 | 68, 119, 2028.0 | 62, 99, 1672.0 |
| `region_revenue` for ACT (customers, orders, revenue) | 4, 5, 75.0 | 5, 8, 119.0 | 5, 8, 119.0 |
| `region_revenue` for QLD | 10, 20, 338.0 | 12, 26, 442.0 | 11, 21, 385.0 |
| `region_revenue` for WA | 13, 18, 311.0 | 15, 24, 462.0 | 14, 21, 418.0 |
| `region_revenue` for NSW, VIC | 314.0, 399.0 | 314.0, 399.0 | 210.0, 248.0 |
| `count(*)`, `count(distinct customer_id)` from `raw_customer_regions` | 100, 100 | 106, 100 | |
| `sum(orders.amount)`, `count(distinct orders.customer_id)` | 1672.0, 62 | 1672.0, 62 | |

**Key number:** `select round(sum(revenue), 2) from region_revenue` is 1672.0 clean and 2028.0 broken (checked by `check_case.py`).

**Correct fix:** keep the latest row per customer (`updated_at desc`) before the join, in `region_revenue` or in a
`stg_customer_regions` model with a `unique` test on `customer_id`. Guards: that `unique` test on the deduplicated
model, or a singular test that `sum(region_revenue.revenue)` equals `sum(orders.amount)` and `sum(customers)` equals
the number of customers with orders.
