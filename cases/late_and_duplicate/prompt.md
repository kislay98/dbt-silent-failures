You are debugging a dbt project (dbt-core 1.12, dbt-duckdb adapter). `dbt build` completes with every test passing,
but finance says the numbers are wrong. Last night's load backfilled a store's February orders, and finance has
reconciled the processor settlement for the whole period at 1737.00. `sum(orders.amount)` is 1917.00, and the daily
revenue dashboard, which reads `daily_revenue`, totals 1686.00. Order 1 shows 20.00 but the customer was charged 10.00.

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
select o.order_date, count(*) as in_orders, coalesce(d.orders, 0) as in_daily_revenue, sum(o.amount) as orders_amount, coalesce(d.revenue, 0) as daily_revenue from orders o left join daily_revenue d using (order_date) group by 1, 3, 5 having count(*) <> coalesce(d.orders, 0) or abs(sum(o.amount) - coalesce(d.revenue, 0)) > 0.005 order by 1
```

```sql
select count(*) as payments, count(distinct order_id) as orders_with_payments, sum(amount) as total_amount from stg_payments
```

```sql
select table_name, column_name, data_type from information_schema.columns where table_name like 'raw_%' order by table_name, ordinal_position
```
