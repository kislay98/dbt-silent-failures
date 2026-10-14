You are debugging a dbt project (dbt-core 1.12, dbt-duckdb adapter). `dbt build` completes with every test passing,
but ops says the daily revenue dashboard (`daily_revenue`) and `orders` disagree about February: 415.00 against
474.00 and 27 orders against 32, and the two have always matched. The extra orders are expected: one store's sync was
down through February and its backfill arrived in last night's load, six completed orders worth 65.00 that the store's
sync screen lists on 1, 3, 8, 13, 22 and 26 February, the first of them placed at 2018-02-01 04:48:19. Ops reconciles
`orders` by `order_date` against the shop admin, which shows orders in the shop's local time, and until this load the two
agreed on every day since January.

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
select strftime(order_date, '%Y-%m') as month, count(*) as orders, round(sum(amount), 2) as amount from orders group by 1 order by 1
```

```sql
select strftime(order_date, '%Y-%m') as month, sum(orders) as orders, round(sum(revenue), 2) as revenue from daily_revenue group by 1 order by 1
```

```sql
select o.order_date, count(*) as in_orders, coalesce(d.orders, 0) as in_daily_revenue, round(sum(o.amount), 2) as orders_amount, coalesce(d.revenue, 0) as daily_revenue from orders o left join daily_revenue d using (order_date) group by 1, 3, 5 having count(*) <> coalesce(d.orders, 0) or abs(sum(o.amount) - coalesce(d.revenue, 0)) > 0.005 order by 1
```

```sql
select order_id, customer_id, ordered_at, order_date, status from stg_orders where order_id >= 97 order by order_id
```

```sql
select customer_id, first_order, most_recent_order, number_of_orders, round(customer_lifetime_value, 2) as lifetime_value from customers where customer_id in (2, 15, 28, 41, 60, 77) order by 1
```

```sql
select table_name, column_name, data_type from information_schema.columns where table_name like 'raw_%' order by table_name, ordinal_position
```
