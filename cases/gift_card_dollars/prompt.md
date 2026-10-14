You are debugging a dbt project (dbt-core 1.12, dbt-duckdb adapter). `dbt build` completes with every test passing,
but finance says the numbers are wrong: the processor settlement total for these orders is 1672.00, while
`sum(orders.amount)` is 1469.05. The gift card provider's redemption statement totals 205.00, while
`sum(orders.gift_card_amount)` is 2.05. Order 9 shows amount 0.23 but the customer redeemed a 23.00 gift card.

Below are the full project files (models, schema tests, seeds), the `dbt build` log, and a sample of the output tables.
Answer with:
1. The root cause, naming the exact file(s) and rows.
2. A fix (code or data change) that makes the output correct again.
3. A test that would have caught this.

## Included
Seeds: shown

## Queries
```sql
select count(*) as orders, sum(amount) as total_amount, sum(gift_card_amount) as gift_card_total from orders
```

```sql
select order_id, customer_id, order_date, status, credit_card_amount, coupon_amount, bank_transfer_amount, gift_card_amount, amount from orders where order_id <= 10 or gift_card_amount > 0 order by order_id
```

```sql
select payment_method, count(*) as payments, sum(amount) as total from stg_payments group by 1 order by 1
```

```sql
select * from customers where customer_id in (11, 22, 46, 53, 66) order by customer_id
```
