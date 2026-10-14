You are debugging a dbt project (dbt-core 1.12, dbt-duckdb adapter). `dbt build` completes with every test passing,
but finance says the regional revenue report is wrong: `sum(region_revenue.revenue)` is 2028.00 against
`sum(orders.amount)` of 1672.00, which reconciles to the processor settlement, and the customers per region add up to
68 while 62 customers have placed an order. Finance reports revenue by each customer's current region.

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
select region, customers, orders, revenue from region_revenue order by region
```

```sql
select sum(revenue) as revenue, sum(orders) as orders, sum(customers) as customers from region_revenue
```

```sql
select count(*) as orders, count(distinct customer_id) as customers, round(sum(amount), 2) as total_amount from orders
```

```sql
select table_name, column_name, data_type from information_schema.columns where table_name like 'raw_%' order by table_name, ordinal_position
```
