You are debugging a dbt project (dbt-core 1.12, dbt-duckdb adapter). `dbt build` completes with every test passing,
but marketing says coupon revenue is wrong: their redemption ledger has coupon payments of 45.00 in March and 33.00
in April, while `orders.coupon_amount` sums to 18.27 and 0.33 for those months. January and February agree. The
processor settlement for March is 622.00 against `sum(orders.amount)` of 595.27 for March.

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
select date_trunc('month', order_date) as month, count(*) filter (where coupon_amount > 0) as coupon_orders, round(sum(coupon_amount), 2) as coupon_amount, round(sum(amount), 2) as amount from orders group by 1 order by 1
```

```sql
select order_id, order_date, status, coupon_amount, amount from orders where coupon_amount > 0 order by order_id
```

```sql
select count(*) as orders, round(sum(amount), 2) as total_amount from orders
```

```sql
select table_name, column_name, data_type from information_schema.columns where table_name like 'raw_%' order by table_name, ordinal_position
```
