You are debugging a dbt project (dbt-core 1.12, dbt-duckdb adapter). `dbt build` completes with every test passing,
but finance says March revenue is wrong: by order date, `orders` has 473.00 across 27 orders for March, while the
daily revenue dashboard (`daily_revenue`) and the shop admin both have 622.00 across 35 orders. January, February and
April agree everywhere. Two customers who ordered in March show no first order date in `customers`.

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
select date_trunc('month', order_date) as month, count(*) as orders, round(sum(amount), 2) as amount from orders group by 1 order by 1
```

```sql
select order_date, orders, round(revenue, 2) as revenue from daily_revenue where order_date between '2018-03-08' and '2018-03-18' order by 1
```

```sql
select order_id, customer_id, ordered_at, order_date, status from stg_orders where order_id between 63 and 76 order by order_id
```

```sql
select customer_id, first_order, most_recent_order, number_of_orders from customers where customer_id in (3, 19, 36, 79) order by 1
```

```sql
select table_name, column_name, data_type from information_schema.columns where table_name like 'raw_%' order by table_name, ordinal_position
```
