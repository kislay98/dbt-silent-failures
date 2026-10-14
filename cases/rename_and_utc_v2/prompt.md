You are debugging a dbt project (dbt-core 1.12, dbt-duckdb adapter). `dbt build` completes with every test passing,
but ops says the orders report lost March and April: the monthly report from `orders` shows January and February
only, and 43 orders have no `order_date`. `daily_revenue` still shows March and April as before. Every morning ops
reconciles `orders` by `order_date` against the app's admin screen, which shows orders in the shop's local time, and
until this load the two agreed on every day since January. Order 73 is placed at 2018-03-16 01:15:20 on the admin screen; in the warehouse `raw_orders` has it with `ordered_at`
empty and `order_ts` 2018-03-15 19:45:20, and it has no `order_date`. `sum(orders.amount)` is 1672.00 in both.

Below are the project files (models and schema tests, without the seed data), the log of the nightly `dbt build`
(the raw tables are loaded by the ingestion job, so the log covers models and tests), the columns of the raw tables
as loaded in the warehouse, and a sample of the output tables.
Answer with:
1. The root cause, naming the exact file(s) and what is wrong in the data.
2. A fix (code or data change) that makes the output correct again.
3. A test that would have caught this.

## Included
Seeds: hidden
Log: models only
