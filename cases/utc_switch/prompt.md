You are debugging a dbt project (dbt-core 1.12, dbt-duckdb adapter). `dbt build` completes with every test passing,
but ops says the daily order counts are wrong. Every morning they reconcile `orders` by `order_date` against the app's
admin screen, which shows orders in the shop's local time, and the two always agreed. Since the middle of March they
disagree on some days: 26 March has 5 orders here against 3 in the app, 27 March 1
against 2, 28 March 0 against 1. Monthly totals still match. Order 73 is
placed at 2018-03-16 01:15:20 on the admin screen; here `stg_orders.ordered_at` is 2018-03-15 19:45:20 and `order_date` is
2018-03-15. `sum(orders.amount)` is 1672.00 in both.

Below are the project files (models and schema tests, without the seed data), the `dbt build` log, the columns of the
raw tables as loaded in the warehouse, and a sample of the output tables.
Answer with:
1. The root cause, naming the exact file(s) and what is wrong in the data.
2. A fix (code or data change) that makes the output correct again.
3. A test that would have caught this.

## Included
Seeds: hidden

## Queries
```sql
select count(*) as orders, sum(amount) as total_amount from orders
```

```sql
select strftime(order_date, '%Y-%m') as month, count(*) as orders, sum(amount) as amount from orders group by 1 order by 1
```

```sql
select order_date, count(*) as orders from orders where order_date between '2018-03-22' and '2018-03-29' group by 1 order by 1
```

```sql
select order_id, ordered_at, order_date from stg_orders where order_date between '2018-03-23' and '2018-03-28' order by ordered_at
```

```sql
select date_trunc('week', order_date) as week, count(*) as orders, round(avg(hour(ordered_at) + minute(ordered_at) / 60.0), 1) as avg_hour from stg_orders group by 1 order by 1
```

```sql
select table_name, column_name, data_type from information_schema.columns where table_name like 'raw_%' order by table_name, ordinal_position
```
