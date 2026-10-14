You are debugging a dbt project (dbt-core 1.12, dbt-duckdb adapter). `dbt build` completes with every test passing,
but finance says completed revenue is wrong: `sum(customers.completed_lifetime_value)` is 824.00 after this week's
build, while last week's report had 1103.00 for the same orders and the shop admin still shows 1103.00 of completed
orders since January (279.00 of them in March). Nothing has been returned or cancelled since. `sum(orders.amount)`
is still 1672.00.

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
select strftime(order_date, '%Y-%m') as month, count(*) as orders, count(status) as with_status, round(sum(amount), 2) as amount from orders group by 1 order by 1
```

```sql
select status, count(*) as orders, round(sum(amount), 2) as amount from orders group by status order by status nulls last
```

```sql
select * from raw_orders where id between 54 and 61 or id in (66, 67, 72, 79, 83, 90, 95) order by id
```

```sql
select customer_id, number_of_orders, round(customer_lifetime_value, 2) as lifetime_value, round(completed_lifetime_value, 2) as completed_lifetime_value from customers where customer_id in (3, 8, 19, 36, 53, 79) order by 1
```

```sql
select table_name, column_name, data_type from information_schema.columns where table_name like 'raw_%' order by table_name, ordinal_position
```
