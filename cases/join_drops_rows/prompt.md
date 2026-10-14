You are debugging a dbt project (dbt-core 1.12, dbt-duckdb adapter). `dbt build` completes with every test passing,
but finance says the numbers are wrong: the processor settlement for these orders is 1672.00, which
`sum(stg_payments.amount)` matches, while `sum(orders.amount)` is 1532.00 and the regional revenue report sums to
1532.00 as well. Order 25 shows 42.00 but the customer paid 58.00 across three payments.

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
select count(*) as payments, sum(amount) as total_amount from stg_payments
```

```sql
select sum(revenue) as revenue, sum(orders) as orders, sum(customers) as customers from region_revenue
```

```sql
select count(*) as payments, sum(p.amount) as amount from stg_payments p left join stg_orders o using (order_id) where o.order_id is null
```

```sql
select table_name, column_name, data_type from information_schema.columns where table_name like 'raw_%' order by table_name, ordinal_position
```
