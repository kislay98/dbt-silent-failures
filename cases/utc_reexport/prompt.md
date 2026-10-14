You are debugging a dbt project (dbt-core 1.12, dbt-duckdb adapter). `dbt build` completes with every test passing,
but ops says the daily order counts are wrong. The shop runs in India and the admin screen shows orders by local day.
Ops compared the admin screen with `orders` for two windows and found disagreements in both, 5 to 18 January:
admin 5th 2, 7th 1, 9th 1, 11th 1, 12th 1, 14th 2, 15th 1, 17th 3, 18th 2; and 20 to 31 March: admin 20th 1,
21st 1, 23rd 4, 24th 2, 26th 3, 27th 2, 28th 1, 30th 1, 31st 1. One example: order 73 was placed on 16 March at 01:15
on the admin screen and the warehouse has it on 15 March. Monthly totals agree. The daily revenue dashboard
(`daily_revenue`) agrees with the admin screen for January to March but not with `orders`.

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
select order_date, count(*) as orders from orders where order_date between '2018-01-05' and '2018-01-18' group by 1 order by 1
```

```sql
select order_date, count(*) as orders from orders where order_date between '2018-03-20' and '2018-03-31' group by 1 order by 1
```

```sql
select order_id, customer_id, ordered_at, order_date, status from stg_orders where order_id in (1, 12, 16, 73, 82, 87, 88, 89, 99) order by order_id
```

```sql
select o.order_date, o.orders as warehouse_orders, d.orders as dashboard_orders from (select order_date, count(*) as orders from orders group by 1) o full outer join daily_revenue d using (order_date) where o.orders is distinct from d.orders order by 1
```

```sql
select table_name, column_name, data_type from information_schema.columns where table_name like 'raw_%' order by table_name, ordinal_position
```
