You are debugging a dbt project (dbt-core 1.12, dbt-duckdb adapter). `dbt build` completes with every test passing,
but finance says the order totals are wrong on every method: `sum(orders.amount)` is 1635.19 against the 1672.00
processor settlement, credit cards are 980.00 against 871.00 on the processor statement, bank transfers 468.00
against 411.00, and gift cards 2.19 against 205.00 on the provider's redemption statement. Coupons match. Order 1
shows 20.00 for a customer who was charged 10.00.

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
select date_trunc('month', order_date) as month, round(sum(credit_card_amount), 2) as credit_card, round(sum(coupon_amount), 2) as coupon, round(sum(bank_transfer_amount), 2) as bank_transfer, round(sum(gift_card_amount), 2) as gift_card, round(sum(amount), 2) as amount from orders group by 1 order by 1
```

```sql
select order_id, customer_id, order_date, status, credit_card_amount, coupon_amount, bank_transfer_amount, gift_card_amount, amount from orders where order_id <= 12 or gift_card_amount > 0 order by order_id
```

```sql
select payment_id, order_id, payment_method, amount from stg_payments where order_id in (1, 9, 10, 18, 19, 62) order by order_id, payment_id
```

```sql
select payment_method, count(*) as payments, round(sum(amount), 2) as total from stg_payments group by 1 order by 1
```

```sql
select table_name, column_name, data_type from information_schema.columns where table_name like 'raw_%' order by table_name, ordinal_position
```
