You are debugging a dbt project (dbt-core 1.12, dbt-duckdb adapter). `dbt build` completes with every test passing,
but finance says February revenue is wrong. The daily revenue dashboard reads `daily_revenue` and shows 415.00
for February and 1672.00 overall; `orders` says 480.00 for February and 1737.00 overall, and the
two have always matched. Nothing failed in last night's run.

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
select sum(orders) as orders, sum(revenue) as revenue from daily_revenue
```

```sql
select strftime(order_date, '%Y-%m') as month, count(*) as orders, sum(amount) as amount from orders group by 1 order by 1
```

```sql
select strftime(order_date, '%Y-%m') as month, sum(orders) as orders, sum(revenue) as revenue from daily_revenue group by 1 order by 1
```

```sql
select o.order_date, count(*) as in_orders, coalesce(d.orders, 0) as in_daily_revenue from orders o left join daily_revenue d using (order_date) group by 1, 3 having count(*) <> coalesce(d.orders, 0) order by 1
```

```sql
select table_name, column_name, data_type from information_schema.columns where table_name like 'raw_%' order by table_name, ordinal_position
```
