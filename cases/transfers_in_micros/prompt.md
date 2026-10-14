You are debugging a dbt project (dbt-core 1.12, dbt-duckdb adapter). `dbt build` completes with every test passing,
but finance says bank transfer revenue is absurd: `orders` shows 1,140,048.00 of bank transfers in March and
390,000.00 in April, against 162.00 and 39.00 on the processor statement. January, February and the first half of
March are right. Order 73 shows an amount of 30,000.00 and the customer paid 3.00.

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
select date_trunc('month', order_date) as month, round(sum(bank_transfer_amount), 2) as bank_transfer, round(sum(amount), 2) as amount from orders group by 1 order by 1
```

```sql
select order_id, order_date, status, bank_transfer_amount, amount from orders where bank_transfer_amount > 0 and order_date >= '2018-03-01' order by order_id
```

```sql
select payment_id, order_id, payment_method, amount from stg_payments where order_id in (64, 67, 73, 77, 92) order by order_id, payment_id
```

```sql
select table_name, column_name, data_type from information_schema.columns where table_name like 'raw_%' order by table_name, ordinal_position
```
