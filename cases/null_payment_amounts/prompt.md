You are debugging a dbt project (dbt-core 1.12, dbt-duckdb adapter). `dbt build` completes with every test passing,
but finance says the order totals are wrong: the processor settlement for January to March is 1533.00 while
`orders.amount` sums to 1468.00 for those months, and order 25 was settled for 58.00 but shows 42.00. The daily
revenue dashboard (`daily_revenue`) still agrees with the settlement.

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
select o.month, o.orders, o.amount, d.daily_revenue from (select date_trunc('month', order_date) as month, count(*) as orders, round(sum(amount), 2) as amount from orders group by 1) o left join (select date_trunc('month', order_date) as month, round(sum(revenue), 2) as daily_revenue from daily_revenue group by 1) d using (month) order by 1
```

```sql
select order_id, customer_id, order_date, status, credit_card_amount, coupon_amount, bank_transfer_amount, gift_card_amount, amount from orders where order_id in (13, 25, 54, 58) order by order_id
```

```sql
select payment_id, order_id, payment_method, amount from stg_payments where order_id in (13, 25, 54, 58) order by order_id, payment_id
```

```sql
select table_name, column_name, data_type from information_schema.columns where table_name like 'raw_%' order by table_name, ordinal_position
```
