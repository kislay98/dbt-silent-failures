You are debugging a dbt project (dbt-core 1.12, dbt-duckdb adapter). `dbt build` completes with every test passing,
but ops says the order counts are wrong: the shop admin shows 99 orders placed since 1 January and the `orders` table
has 115, and the processor settlement for those orders is 1672.00 against `sum(orders.amount)` of 1913.00. The daily
revenue dashboard (`daily_revenue`) still shows 99 orders and 1672.00, and it disagrees with `orders` for a run of days
in February. Customer 51 shows five orders in `customers`; the admin has three for them.

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
select count(*) as orders, round(sum(amount), 2) as total_amount from orders
```

```sql
select order_date, count(*) as orders, round(sum(amount), 2) as amount from orders where order_date between '2018-02-08' and '2018-02-26' group by 1 order by 1
```

```sql
select order_date, orders, round(revenue, 2) as revenue from daily_revenue where order_date between '2018-02-08' and '2018-02-26' order by 1
```

```sql
select order_id, customer_id, order_date, status, amount from orders where customer_id in (1, 51) order by order_id
```

```sql
select table_name, column_name, data_type from information_schema.columns where table_name like 'raw_%' order by table_name, ordinal_position
```
