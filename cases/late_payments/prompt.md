You are debugging a dbt project (dbt-core 1.12, dbt-duckdb adapter). `dbt build` completes with every test passing,
but finance cannot close February: `orders` has 485.00 of February revenue and the daily revenue dashboard
(`daily_revenue`) has 415.00, with the same 27 orders on both sides. The processor settlement for February is 485.00.
January and March agree on both sides.

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
select o.order_date, count(*) as orders, round(sum(o.amount), 2) as orders_amount, d.orders as daily_orders, round(d.revenue, 2) as daily_revenue from orders o join daily_revenue d using (order_date) where o.order_date between '2018-02-01' and '2018-02-28' group by 1, d.orders, d.revenue order by 1
```

```sql
select payment_id, order_id, payment_method, amount from stg_payments where order_id in (30, 33, 36) order by order_id, payment_id
```

```sql
select round(sum(amount), 2) as orders_amount, (select round(sum(revenue), 2) from daily_revenue) as daily_revenue, (select max(order_date) from daily_revenue) as last_day from orders
```

```sql
select table_name, column_name, data_type from information_schema.columns where table_name like 'raw_%' order by table_name, ordinal_position
```
