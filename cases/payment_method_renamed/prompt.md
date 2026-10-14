You are debugging a dbt project (dbt-core 1.12, dbt-duckdb adapter). `dbt build` completes with every test passing,
but finance says the payment mix report is wrong: for March, `orders` shows 0.00 by credit card, coupon, bank
transfer and gift card while the month's total is 622.00, and the processor's statement has 334.00 of card volume in
March. January and February look right. Total revenue by month matches the settlement.

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
select order_id, order_date, status, credit_card_amount, coupon_amount, bank_transfer_amount, gift_card_amount, amount from orders where order_date between '2018-02-24' and '2018-03-06' order by order_id
```

```sql
select count(*) as orders, round(sum(amount), 2) as total_amount from orders
```

```sql
select table_name, column_name, data_type from information_schema.columns where table_name like 'raw_%' order by table_name, ordinal_position
```
