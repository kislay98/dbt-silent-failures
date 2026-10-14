You are debugging a dbt project (dbt-core 1.12, dbt-duckdb adapter). `dbt build` completes with every test passing,
but finance says the order totals are wrong: `sum(orders.amount)` is 2083.00 against the 1672.00 processor
settlement, and the bank transfer column is 822.00 for January to April against 411.00 on the bank statements, in
every month. Credit cards, coupons and gift cards match their statements. Order 5 shows a bank transfer of 34.00 for
a customer who paid 17.00. `region_revenue` and `customers.customer_lifetime_value` still total 1672.00.

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
select date_trunc('month', order_date) as month, round(sum(credit_card_amount), 2) as credit_card, round(sum(coupon_amount), 2) as coupon, round(sum(bank_transfer_amount), 2) as bank_transfer, round(sum(gift_card_amount), 2) as gift_card, round(sum(amount), 2) as amount, round(sum(fees), 2) as fees from orders group by 1 order by 1
```

```sql
select order_id, customer_id, order_date, status, credit_card_amount, bank_transfer_amount, amount, fees from orders where order_id in (5, 10, 13, 25, 54) order by order_id
```

```sql
select payment_id, order_id, payment_method, amount from stg_payments where order_id in (5, 10, 13, 25) order by order_id, payment_id
```

```sql
select payment_method, count(*) as payments, round(sum(amount), 2) as total from stg_payments group by 1 order by 1
```

```sql
select table_name, column_name, data_type from information_schema.columns where table_name like 'raw_%' order by table_name, ordinal_position
```
