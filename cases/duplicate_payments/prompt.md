You are debugging a dbt project (dbt-core 1.12, dbt-duckdb adapter). `dbt build` completes with every test passing,
but finance says the numbers are wrong: the processor settlement total for these orders is 1672.00, while
`sum(orders.amount)` is 1852.00. Order 1 shows amount 20.00 but the customer was charged 10.00.

Below are the full project files (models, schema tests, seeds), the `dbt build` log, and a sample of the output tables.
Answer with:
1. The root cause, naming the exact file(s) and rows.
2. A fix (code or data change) that makes the output correct again.
3. A test that would have caught this.

## Included
Seeds: shown

## Queries
```sql
select count(*) as orders, sum(amount) as total_amount from orders
```

```sql
select order_id, customer_id, order_date, status, credit_card_amount, coupon_amount, bank_transfer_amount, gift_card_amount, amount from orders where order_id <= 12 order by order_id
```

```sql
select * from customers where customer_id in (1,3,7,64,99) order by customer_id
```
