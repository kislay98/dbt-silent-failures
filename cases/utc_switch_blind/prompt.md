You are debugging a dbt project (dbt-core 1.12, dbt-duckdb adapter). `dbt build` completes with every test passing,
but ops says the daily order counts are wrong. The shop operates in India. Every morning ops reconcile `orders` by
`order_date` against the app's admin screen, which shows orders in local time, and the two always agreed. Since the
middle of March some days disagree. The admin screen's daily counts from 1 March on (days with no orders omitted):

| Day | Orders |
|---|---|
| 2018-03-01 | 2 |
| 2018-03-02 | 1 |
| 2018-03-03 | 2 |
| 2018-03-05 | 1 |
| 2018-03-06 | 1 |
| 2018-03-07 | 1 |
| 2018-03-08 | 1 |
| 2018-03-10 | 1 |
| 2018-03-11 | 3 |
| 2018-03-12 | 2 |
| 2018-03-14 | 1 |
| 2018-03-16 | 1 |
| 2018-03-17 | 1 |
| 2018-03-18 | 1 |
| 2018-03-20 | 1 |
| 2018-03-21 | 1 |
| 2018-03-23 | 4 |
| 2018-03-24 | 2 |
| 2018-03-26 | 3 |
| 2018-03-27 | 2 |
| 2018-03-28 | 1 |
| 2018-03-30 | 1 |
| 2018-03-31 | 1 |
| 2018-04-02 | 1 |
| 2018-04-03 | 2 |
| 2018-04-04 | 1 |
| 2018-04-06 | 1 |
| 2018-04-07 | 2 |
| 2018-04-09 | 1 |

Monthly totals still match and `sum(orders.amount)` is 1672.00 in both.

Below are the project files (models and schema tests, without the seed data), the `dbt build` log, the columns of the
raw tables as loaded in the warehouse, and a sample of the output tables.
Answer with:
1. The root cause, naming the exact file(s) and what is wrong in the data.
2. A fix (code or data change) that makes the output correct again.
3. A test that would have caught this.

## Included
Seeds: hidden

## Queries
```sql
select count(*) as orders, sum(amount) as total_amount from orders
```

```sql
select strftime(order_date, '%Y-%m') as month, count(*) as orders, sum(amount) as amount from orders group by 1 order by 1
```

```sql
select order_date, count(*) as orders from orders where order_date >= '2018-03-01' group by 1 order by 1
```

```sql
select order_id, ordered_at, order_date from stg_orders where order_date between '2018-03-23' and '2018-03-28' order by ordered_at
```

```sql
select table_name, column_name, data_type from information_schema.columns where table_name like 'raw_%' order by table_name, ordinal_position
```
