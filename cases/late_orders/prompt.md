You are debugging a dbt project (dbt-core 1.12, dbt-duckdb adapter). `dbt build` completes with every test passing,
but finance says February revenue is wrong. The daily revenue dashboard reads `daily_revenue` and shows 415.00
for February and 1672.00 overall; `orders` says 480.00 for February and 1737.00 overall, and the
two have always matched. Nothing failed in last night's run.

Below are the project files (models and schema tests, without the seed data), the `dbt build` log, the columns of the
raw tables as loaded in the warehouse, and a sample of the output tables.
Answer with:
1. The root cause, naming the exact file(s) and what is wrong in the data.
2. A fix (code or data change) that makes the output correct again.
3. A test that would have caught this.

===== jaffle_shop/dbt_project.yml =====
name: 'jaffle_shop'

config-version: 2
version: '0.1'

profile: 'jaffle_shop'

model-paths: ["models"]
seed-paths: ["seeds"]
test-paths: ["tests"]
analysis-paths: ["analysis"]
macro-paths: ["macros"]

target-path: "target"
clean-targets:
    - "target"
    - "dbt_modules"
    - "logs"

require-dbt-version: [">=1.11.0", "<2.0.0"]

seeds: 
  +docs:
    node_color: '#cd7f32'

models:
  jaffle_shop:
    +materialized: table
    staging:
      +materialized: view
      +docs:
        node_color: 'silver'
    +docs:
      node_color: 'gold'

===== jaffle_shop/models/customers.sql =====
with customers as (

    select * from {{ ref('stg_customers') }}

),

orders as (

    select * from {{ ref('stg_orders') }}

),

payments as (

    select * from {{ ref('stg_payments') }}

),

customer_orders as (

        select
        customer_id,

        min(order_date) as first_order,
        max(order_date) as most_recent_order,
        count(order_id) as number_of_orders
    from orders

    group by customer_id

),

customer_payments as (

    select
        orders.customer_id,
        sum(amount) as total_amount,
        sum(case when orders.status = 'completed' then amount else 0 end) as completed_amount

    from payments

    left join orders on
         payments.order_id = orders.order_id

    group by orders.customer_id

),

final as (

    select
        customers.customer_id,
        customers.first_name,
        customers.last_name,
        customer_orders.first_order,
        customer_orders.most_recent_order,
        customer_orders.number_of_orders,
        customer_payments.total_amount as customer_lifetime_value,
        customer_payments.completed_amount as completed_lifetime_value

    from customers

    left join customer_orders
        on customers.customer_id = customer_orders.customer_id

    left join customer_payments
        on  customers.customer_id = customer_payments.customer_id

)

select * from final

===== jaffle_shop/models/daily_revenue.sql =====
{{
    config(
        materialized='incremental',
        unique_key='order_date',
        incremental_strategy='delete+insert'
    )
}}

with orders as (

    select * from {{ ref('orders') }}

),

final as (

    select
        order_date,
        count(*) as orders,
        sum(amount) as revenue

    from orders

    {% if is_incremental() %}
    -- payments can land a day or two after the order, so the last three days are rebuilt on every run
    where order_date >= (select max(order_date) - interval 3 day from {{ this }})
    {% endif %}

    group by order_date

)

select * from final

===== jaffle_shop/models/orders.sql =====
{% set payment_methods = ['credit_card', 'coupon', 'bank_transfer', 'gift_card'] %}

with orders as (

    select * from {{ ref('stg_orders') }}

),

payments as (

    select * from {{ ref('stg_payments') }}

),

order_payments as (

    select
        order_id,

        {% for payment_method in payment_methods -%}
        sum(case when payment_method = '{{ payment_method }}' then amount else 0 end) as {{ payment_method }}_amount,
        {% endfor -%}

        sum(amount) as total_amount

    from payments

    group by order_id

),

final as (

    select
        orders.order_id,
        orders.customer_id,
        orders.order_date,
        orders.status,

        {% for payment_method in payment_methods -%}

        order_payments.{{ payment_method }}_amount,

        {% endfor -%}

        order_payments.total_amount as amount

    from orders


    left join order_payments
        on orders.order_id = order_payments.order_id

)

select * from final

===== jaffle_shop/models/region_revenue.sql =====
with payments as (

    select * from {{ ref('stg_payments') }}

),

orders as (

    select * from {{ ref('stg_orders') }}

),

regions as (

    {#-
    The regions feed is loaded as a seed, like the other raw tables
    #}
    select * from {{ ref('raw_customer_regions') }}

),

final as (

    select
        regions.region,
        count(distinct orders.customer_id) as customers,
        count(distinct orders.order_id) as orders,
        sum(payments.amount) as revenue

    from payments

    left join orders
        on payments.order_id = orders.order_id

    left join regions
        on orders.customer_id = regions.customer_id

    group by regions.region

)

select * from final

===== jaffle_shop/models/schema.yml =====
version: 2

models:
  - name: customers
    description: This table has basic information about a customer, as well as some derived facts based on a customer's orders

    columns:
      - name: customer_id
        description: This is a unique identifier for a customer
        tests:
          - unique
          - not_null

      - name: first_name
        description: Customer's first name. PII.

      - name: last_name
        description: Customer's last name. PII.

      - name: first_order
        description: Date (UTC) of a customer's first order

      - name: most_recent_order
        description: Date (UTC) of a customer's most recent order

      - name: number_of_orders
        description: Count of the number of orders a customer has placed

      - name: total_order_amount
        description: Total value (AUD) of a customer's orders

      - name: completed_lifetime_value
        description: Total value (AUD) of a customer's orders with status completed

  - name: orders
    description: This table has basic information about orders, as well as some derived facts based on payments

    columns:
      - name: order_id
        tests:
          - unique
          - not_null
        description: This is a unique identifier for an order

      - name: customer_id
        description: Foreign key to the customers table
        tests:
          - not_null
          - relationships:
              arguments:
                to: ref('customers')
                field: customer_id

      - name: order_date
        description: Date (UTC) that the order was placed, from the ordered_at timestamp

      - name: status
        description: '{{ doc("orders_status") }}'
        tests:
          - accepted_values:
              arguments:
                values: ['placed', 'shipped', 'completed', 'return_pending', 'returned']

      - name: amount
        description: Total amount (AUD) of the order
        tests:
          - not_null

      - name: credit_card_amount
        description: Amount of the order (AUD) paid for by credit card
        tests:
          - not_null

      - name: coupon_amount
        description: Amount of the order (AUD) paid for by coupon
        tests:
          - not_null

      - name: bank_transfer_amount
        description: Amount of the order (AUD) paid for by bank transfer
        tests:
          - not_null

      - name: gift_card_amount
        description: Amount of the order (AUD) paid for by gift card
        tests:
          - not_null

  - name: daily_revenue
    description: Orders and revenue per order date. Incremental, rebuilds the last three days on each run

    columns:
      - name: order_date
        tests:
          - unique
          - not_null

      - name: orders
        tests:
          - not_null

      - name: revenue
        tests:
          - not_null

  - name: region_revenue
    description: Customers, orders and revenue per customer region

    columns:
      - name: region
        tests:
          - unique
          - not_null

      - name: revenue
        tests:
          - not_null

seeds:
  - name: raw_customer_regions
    description: One row per customer from the regions feed

    columns:
      - name: customer_id
        tests:
          - not_null

      - name: region
        tests:
          - not_null

===== jaffle_shop/models/staging/schema.yml =====
version: 2

models:
  - name: stg_customers
    columns:
      - name: customer_id
        tests:
          - unique
          - not_null

  - name: stg_orders
    columns:
      - name: order_id
        tests:
          - unique
          - not_null
      - name: status
        tests:
          - accepted_values:
              arguments:
                values: ['placed', 'shipped', 'completed', 'return_pending', 'returned']

  - name: stg_payments
    columns:
      - name: payment_id
        tests:
          - unique
          - not_null
      - name: payment_method
        tests:
          - accepted_values:
              arguments:
                values: ['credit_card', 'coupon', 'bank_transfer', 'gift_card']

===== jaffle_shop/models/staging/stg_customers.sql =====
with source as (

    {#-
    Normally we would select from the table here, but we are using seeds to load
    our data in this project
    #}
    select * from {{ ref('raw_customers') }}

),

renamed as (

    select
        id as customer_id,
        first_name,
        last_name

    from source

)

select * from renamed

===== jaffle_shop/models/staging/stg_orders.sql =====
with source as (

    {#-
    Normally we would select from the table here, but we are using seeds to load
    our data in this project
    #}
    select * from {{ ref('raw_orders') }}

),

renamed as (

    select
        id as order_id,
        user_id as customer_id,
        ordered_at,
        cast(ordered_at as date) as order_date,
        status

    from source

)

select * from renamed

===== jaffle_shop/models/staging/stg_payments.sql =====
with source as (
    
    {#-
    Normally we would select from the table here, but we are using seeds to load
    our data in this project
    #}
    select * from {{ ref('raw_payments') }}

),

renamed as (

    select
        id as payment_id,
        order_id,
        payment_method,

        -- `amount` is currently stored in cents, so we convert it to dollars
        amount / 100 as amount

    from source

)

select * from renamed

===== dbt build log =====
19:57:14  Running with dbt=1.12.5
19:57:14  Registered adapter: duckdb=1.11.0
19:57:14  Found 7 models, 4 seeds, 29 data tests, 500 macros
19:57:14
19:57:14  Concurrency: 24 threads (target='dev')
19:57:14
19:57:14  3 of 40 START seed file main.raw_orders ........................................ [RUN]
19:57:14  4 of 40 START seed file main.raw_payments ...................................... [RUN]
19:57:14  2 of 40 START seed file main.raw_customers ..................................... [RUN]
19:57:14  1 of 40 START seed file main.raw_customer_regions .............................. [RUN]
19:57:15  4 of 40 OK loaded seed file main.raw_payments .................................. [INSERT 119 in 0.07s]
19:57:15  2 of 40 OK loaded seed file main.raw_customers ................................. [INSERT 100 in 0.06s]
19:57:15  1 of 40 OK loaded seed file main.raw_customer_regions .......................... [INSERT 100 in 0.06s]
19:57:15  5 of 40 START sql view model main.stg_payments ................................. [RUN]
19:57:15  8 of 40 START test not_null_raw_customer_regions_region ........................ [RUN]
19:57:15  7 of 40 START test not_null_raw_customer_regions_customer_id ................... [RUN]
19:57:15  6 of 40 START sql view model main.stg_customers ................................ [RUN]
19:57:15  3 of 40 OK loaded seed file main.raw_orders .................................... [INSERT 105 in 0.09s]
19:57:15  9 of 40 START sql view model main.stg_orders ................................... [RUN]
19:57:15  8 of 40 PASS not_null_raw_customer_regions_region .............................. [PASS in 0.08s]
19:57:15  7 of 40 PASS not_null_raw_customer_regions_customer_id ......................... [PASS in 0.08s]
19:57:15  5 of 40 OK created sql view model main.stg_payments ............................ [OK in 0.09s]
19:57:15  11 of 40 START test not_null_stg_payments_payment_id ........................... [RUN]
19:57:15  10 of 40 START test accepted_values_stg_payments_payment_method__credit_card__coupon__bank_transfer__gift_card  [RUN]
19:57:15  12 of 40 START test unique_stg_payments_payment_id ............................. [RUN]
19:57:15  6 of 40 OK created sql view model main.stg_customers ........................... [OK in 0.11s]
19:57:15  13 of 40 START test not_null_stg_customers_customer_id ......................... [RUN]
19:57:15  14 of 40 START test unique_stg_customers_customer_id ........................... [RUN]
19:57:15  9 of 40 OK created sql view model main.stg_orders .............................. [OK in 0.11s]
19:57:15  11 of 40 PASS not_null_stg_payments_payment_id ................................. [PASS in 0.06s]
19:57:15  15 of 40 START test accepted_values_stg_orders_status__placed__shipped__completed__return_pending__returned  [RUN]
19:57:15  16 of 40 START test not_null_stg_orders_order_id ............................... [RUN]
19:57:15  17 of 40 START test unique_stg_orders_order_id ................................. [RUN]
19:57:15  12 of 40 PASS unique_stg_payments_payment_id ................................... [PASS in 0.06s]
19:57:15  10 of 40 PASS accepted_values_stg_payments_payment_method__credit_card__coupon__bank_transfer__gift_card  [PASS in 0.08s]
19:57:15  14 of 40 PASS unique_stg_customers_customer_id ................................. [PASS in 0.06s]
19:57:15  13 of 40 PASS not_null_stg_customers_customer_id ............................... [PASS in 0.07s]
19:57:15  15 of 40 PASS accepted_values_stg_orders_status__placed__shipped__completed__return_pending__returned  [PASS in 0.04s]
19:57:15  16 of 40 PASS not_null_stg_orders_order_id ..................................... [PASS in 0.04s]
19:57:15  17 of 40 PASS unique_stg_orders_order_id ....................................... [PASS in 0.04s]
19:57:15  19 of 40 START sql table model main.orders ..................................... [RUN]
19:57:15  18 of 40 START sql table model main.customers .................................. [RUN]
19:57:15  20 of 40 START sql table model main.region_revenue ............................. [RUN]
19:57:15  19 of 40 OK created sql table model main.orders ................................ [OK in 0.07s]
19:57:15  21 of 40 START test accepted_values_orders_status__placed__shipped__completed__return_pending__returned  [RUN]
19:57:15  23 of 40 START test not_null_orders_bank_transfer_amount ....................... [RUN]
19:57:15  24 of 40 START test not_null_orders_coupon_amount .............................. [RUN]
19:57:15  25 of 40 START test not_null_orders_credit_card_amount ......................... [RUN]
19:57:15  26 of 40 START test not_null_orders_customer_id ................................ [RUN]
19:57:15  27 of 40 START test not_null_orders_gift_card_amount ........................... [RUN]
19:57:15  28 of 40 START test not_null_orders_order_id ................................... [RUN]
19:57:15  29 of 40 START test unique_orders_order_id ..................................... [RUN]
19:57:15  22 of 40 START test not_null_orders_amount ..................................... [RUN]
19:57:15  20 of 40 OK created sql table model main.region_revenue ........................ [OK in 0.11s]
19:57:15  18 of 40 OK created sql table model main.customers ............................. [OK in 0.12s]
19:57:15  31 of 40 START test not_null_region_revenue_revenue ............................ [RUN]
19:57:15  30 of 40 START test not_null_region_revenue_region ............................. [RUN]
19:57:15  32 of 40 START test unique_region_revenue_region ............................... [RUN]
19:57:15  33 of 40 START test not_null_customers_customer_id ............................. [RUN]
19:57:15  34 of 40 START test relationships_orders_customer_id__customer_id__ref_customers_  [RUN]
19:57:15  35 of 40 START test unique_customers_customer_id ............................... [RUN]
19:57:15  23 of 40 PASS not_null_orders_bank_transfer_amount ............................. [PASS in 0.12s]
19:57:15  21 of 40 PASS accepted_values_orders_status__placed__shipped__completed__return_pending__returned  [PASS in 0.12s]
19:57:15  24 of 40 PASS not_null_orders_coupon_amount .................................... [PASS in 0.12s]
19:57:15  28 of 40 PASS not_null_orders_order_id ......................................... [PASS in 0.12s]
19:57:15  26 of 40 PASS not_null_orders_customer_id ...................................... [PASS in 0.12s]
19:57:15  27 of 40 PASS not_null_orders_gift_card_amount ................................. [PASS in 0.12s]
19:57:15  25 of 40 PASS not_null_orders_credit_card_amount ............................... [PASS in 0.12s]
19:57:15  22 of 40 PASS not_null_orders_amount ........................................... [PASS in 0.12s]
19:57:15  29 of 40 PASS unique_orders_order_id ........................................... [PASS in 0.12s]
19:57:15  36 of 40 START sql incremental model main.daily_revenue ........................ [RUN]
19:57:15  30 of 40 PASS not_null_region_revenue_region ................................... [PASS in 0.09s]
19:57:15  32 of 40 PASS unique_region_revenue_region ..................................... [PASS in 0.09s]
19:57:15  31 of 40 PASS not_null_region_revenue_revenue .................................. [PASS in 0.09s]
19:57:15  33 of 40 PASS not_null_customers_customer_id ................................... [PASS in 0.08s]
19:57:15  34 of 40 PASS relationships_orders_customer_id__customer_id__ref_customers_ .... [PASS in 0.10s]
19:57:15  35 of 40 PASS unique_customers_customer_id ..................................... [PASS in 0.10s]
19:57:15  36 of 40 OK created sql incremental model main.daily_revenue ................... [OK in 0.06s]
19:57:15  39 of 40 START test not_null_daily_revenue_revenue ............................. [RUN]
19:57:15  38 of 40 START test not_null_daily_revenue_orders .............................. [RUN]
19:57:15  37 of 40 START test not_null_daily_revenue_order_date .......................... [RUN]
19:57:15  40 of 40 START test unique_daily_revenue_order_date ............................ [RUN]
19:57:15  39 of 40 PASS not_null_daily_revenue_revenue ................................... [PASS in 0.03s]
19:57:15  38 of 40 PASS not_null_daily_revenue_orders .................................... [PASS in 0.03s]
19:57:15  37 of 40 PASS not_null_daily_revenue_order_date ................................ [PASS in 0.04s]
19:57:15  40 of 40 PASS unique_daily_revenue_order_date .................................. [PASS in 0.04s]
19:57:15
19:57:15  Finished running 1 incremental model, 4 seeds, 3 table models, 29 data tests, 3 view models in 0 hours 0 minutes and 0.75 seconds (0.75s).
19:57:15
19:57:15  Completed successfully
19:57:15
19:57:15  Done. PASS=40 WARN=0 ERROR=0 SKIP=0 NO-OP=0 REUSED=0 TOTAL=40

===== output sample =====
```sql
select count(*) as orders, sum(amount) as total_amount from orders
```
```
┌────────┬──────────────┐
│ orders │ total_amount │
│ int64  │    double    │
├────────┼──────────────┤
│    105 │       1737.0 │
└────────┴──────────────┘

```

```sql
select sum(orders) as orders, sum(revenue) as revenue from daily_revenue
```
```
┌────────┬─────────┐
│ orders │ revenue │
│ int128 │ double  │
├────────┼─────────┤
│     99 │  1672.0 │
└────────┴─────────┘

```

```sql
select strftime(order_date, '%Y-%m') as month, count(*) as orders, sum(amount) as amount from orders group by 1 order by 1
```
```
┌─────────┬────────┬────────┐
│  month  │ orders │ amount │
│ varchar │ int64  │ double │
├─────────┼────────┼────────┤
│ 2018-01 │     29 │  496.0 │
│ 2018-02 │     33 │  480.0 │
│ 2018-03 │     35 │  622.0 │
│ 2018-04 │      8 │  139.0 │
└─────────┴────────┴────────┘

```

```sql
select strftime(order_date, '%Y-%m') as month, sum(orders) as orders, sum(revenue) as revenue from daily_revenue group by 1 order by 1
```
```
┌─────────┬────────┬─────────┐
│  month  │ orders │ revenue │
│ varchar │ int128 │ double  │
├─────────┼────────┼─────────┤
│ 2018-01 │     29 │   496.0 │
│ 2018-02 │     27 │   415.0 │
│ 2018-03 │     35 │   622.0 │
│ 2018-04 │      8 │   139.0 │
└─────────┴────────┴─────────┘

```

```sql
select o.order_date, count(*) as in_orders, coalesce(d.orders, 0) as in_daily_revenue from orders o left join daily_revenue d using (order_date) group by 1, 3 having count(*) <> coalesce(d.orders, 0) order by 1
```
```
┌────────────┬───────────┬──────────────────┐
│ order_date │ in_orders │ in_daily_revenue │
│    date    │   int64   │      int64       │
├────────────┼───────────┼──────────────────┤
│ 2018-02-03 │         1 │                0 │
│ 2018-02-08 │         2 │                1 │
│ 2018-02-13 │         2 │                1 │
│ 2018-02-17 │         4 │                3 │
│ 2018-02-22 │         1 │                0 │
│ 2018-02-26 │         3 │                2 │
└────────────┴───────────┴──────────────────┘

```

```sql
select table_name, column_name, data_type from information_schema.columns where table_name like 'raw_%' order by table_name, ordinal_position
```
```
┌──────────────────────┬────────────────┬───────────┐
│      table_name      │  column_name   │ data_type │
│       varchar        │    varchar     │  varchar  │
├──────────────────────┼────────────────┼───────────┤
│ raw_customer_regions │ customer_id    │ INTEGER   │
│ raw_customer_regions │ region         │ VARCHAR   │
│ raw_customer_regions │ updated_at     │ DATE      │
│ raw_customers        │ id             │ INTEGER   │
│ raw_customers        │ first_name     │ VARCHAR   │
│ raw_customers        │ last_name      │ VARCHAR   │
│ raw_orders           │ id             │ INTEGER   │
│ raw_orders           │ user_id        │ INTEGER   │
│ raw_orders           │ ordered_at     │ TIMESTAMP │
│ raw_orders           │ status         │ VARCHAR   │
│ raw_payments         │ id             │ INTEGER   │
│ raw_payments         │ order_id       │ INTEGER   │
│ raw_payments         │ payment_method │ VARCHAR   │
│ raw_payments         │ amount         │ INTEGER   │
└──────────────────────┴────────────────┴───────────┘
  14 rows                                 3 columns

```
