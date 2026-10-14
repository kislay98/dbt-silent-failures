You are debugging a dbt project (dbt-core 1.12, dbt-duckdb adapter). `dbt build` completes with every test passing,
but finance says completed revenue is wrong: the shop admin shows 1168.00 of completed orders since January and 279.00
in March, while `customers.completed_lifetime_value` sums to 967.00 and the March orders marked completed in `orders`
add up to 143.00. Ops says the daily revenue dashboard (`daily_revenue`) and `orders` disagree about February, 415.00
against 480.00 and 27 orders against 33, and the two have always matched. Total revenue matches the admin in every
other month.

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
select date_trunc('month', order_date) as month, status, count(*) as orders, round(sum(amount), 2) as amount from orders group by 1, 2 order by 1, 2
```

```sql
select strftime(order_date, '%Y-%m') as month, sum(orders) as orders, round(sum(revenue), 2) as revenue from daily_revenue group by 1 order by 1
```

```sql
select o.order_date, count(*) as in_orders, coalesce(d.orders, 0) as in_daily_revenue from orders o left join daily_revenue d using (order_date) group by 1, 3 having count(*) <> coalesce(d.orders, 0) order by 1
```

```sql
select order_id, customer_id, order_date, status, amount from orders where order_id >= 98 order by order_id
```

```sql
select customer_id, number_of_orders, round(customer_lifetime_value, 2) as lifetime_value, round(completed_lifetime_value, 2) as completed_lifetime_value from customers where customer_id in (2, 3, 15, 36, 53, 79) order by 1
```

```sql
select table_name, column_name, data_type from information_schema.columns where table_name like 'raw_%' order by table_name, ordinal_position
```
