You are debugging a dbt project (dbt-core 1.12, dbt-duckdb adapter). `dbt build` completes with every test passing,
but finance says completed revenue is wrong: `sum(customers.completed_lifetime_value)` is 604.00 after this
week's build, while the finance ledger has 1103.00 of completed orders for the same period. `sum(orders.amount)` is
still 1672.00.

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
select sum(customer_lifetime_value) as lifetime_value, sum(completed_lifetime_value) as completed_lifetime_value from customers
```

```sql
select status, count(*) as orders, sum(amount) as amount from orders group by status order by status
```

```sql
select strftime(order_date, '%Y-%m') as month, status, count(*) as orders from orders group by 1, 2 order by 1, 2
```

```sql
select table_name, column_name, data_type from information_schema.columns where table_name like 'raw_%' order by table_name, ordinal_position
```
