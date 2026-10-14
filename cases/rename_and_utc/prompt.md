You are debugging a dbt project (dbt-core 1.12, dbt-duckdb adapter). `dbt build` completes with every test passing,
but ops says the orders report lost March and April: the monthly report from `orders` shows January and February
only, and 43 orders have no `order_date`. `daily_revenue` still shows March and April as before. Every morning ops
reconciles `orders` by `order_date` against the app's admin screen, which shows orders in the shop's local time, and
until this load the two agreed on every day since January. Order 73 is placed at 2018-03-16 01:15:20 on the admin
screen. `sum(orders.amount)` is 1672.00 in both.

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

## Queries
```sql
select strftime(order_date, '%Y-%m') as month, count(*) as orders, round(sum(amount), 2) as amount from orders group by 1 order by 1 nulls last
```

```sql
select strftime(order_date, '%Y-%m') as month, sum(orders) as orders, round(sum(revenue), 2) as revenue from daily_revenue group by 1 order by 1
```

```sql
select * from raw_orders where id between 54 and 60 or id in (66, 73, 90, 99) order by id
```

```sql
select customer_id, first_order, most_recent_order, number_of_orders from customers where customer_id in (3, 8, 9, 12, 22, 93) order by 1
```

```sql
select table_name, column_name, data_type from information_schema.columns where table_name like 'raw_%' order by table_name, ordinal_position
```
