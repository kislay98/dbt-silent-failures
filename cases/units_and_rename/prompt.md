You are debugging a dbt project (dbt-core 1.12, dbt-duckdb adapter). `dbt build` completes with every test passing,
but finance says the March payment mix report is wrong: `orders` shows 0.00 for every payment method in March against
a month total of 595.27, and the processor settlement for March is 622.00. April has the same shape: 0.00 by method,
106.33 in total, 139.00 settled. Marketing's coupon ledger has 45.00 of coupons in March and 33.00 in April.
January and February are right on every count.

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
select order_id, order_date, status, credit_card_amount, coupon_amount, bank_transfer_amount, gift_card_amount, amount from orders where order_id between 55 and 62 or order_id in (76, 81, 86, 92, 94, 95) order by order_id
```

```sql
select payment_id, order_id, payment_method, amount from stg_payments where order_id in (56, 58, 76, 92) order by order_id, payment_id
```

```sql
select table_name, column_name, data_type from information_schema.columns where table_name like 'raw_%' order by table_name, ordinal_position
```
