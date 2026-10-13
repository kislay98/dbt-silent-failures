You are debugging a dbt project (dbt-core 1.12, dbt-duckdb adapter). `dbt build` completes with every test passing,
but finance says completed revenue is wrong: `sum(customers.completed_lifetime_value)` is 604.00 after this
week's build, while the finance ledger has 1103.00 of completed orders for the same period. `sum(orders.amount)` is
still 1672.00.

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
                values: ['placed', 'shipped', 'completed', 'return_pending', 'returned', 'Placed', 'Shipped', 'Completed', 'Return_pending', 'Returned']

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
                values: ['placed', 'shipped', 'completed', 'return_pending', 'returned', 'Placed', 'Shipped', 'Completed', 'Return_pending', 'Returned']

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
20:02:25  Running with dbt=1.12.5
20:02:25  Registered adapter: duckdb=1.11.0
20:02:25  Found 7 models, 4 seeds, 29 data tests, 500 macros
20:02:25
20:02:25  Concurrency: 24 threads (target='dev')
20:02:25
20:02:25  2 of 40 START seed file main.raw_customers ..................................... [RUN]
20:02:25  1 of 40 START seed file main.raw_customer_regions .............................. [RUN]
20:02:25  3 of 40 START seed file main.raw_orders ........................................ [RUN]
20:02:25  4 of 40 START seed file main.raw_payments ...................................... [RUN]
20:02:25  2 of 40 OK loaded seed file main.raw_customers ................................. [INSERT 100 in 0.06s]
20:02:25  4 of 40 OK loaded seed file main.raw_payments .................................. [INSERT 113 in 0.04s]
20:02:25  5 of 40 START sql view model main.stg_customers ................................ [RUN]
20:02:25  6 of 40 START sql view model main.stg_payments ................................. [RUN]
20:02:25  3 of 40 OK loaded seed file main.raw_orders .................................... [INSERT 99 in 0.05s]
20:02:25  1 of 40 OK loaded seed file main.raw_customer_regions .......................... [INSERT 100 in 0.06s]
20:02:25  7 of 40 START sql view model main.stg_orders ................................... [RUN]
20:02:25  8 of 40 START test not_null_raw_customer_regions_customer_id ................... [RUN]
20:02:25  9 of 40 START test not_null_raw_customer_regions_region ........................ [RUN]
20:02:25  9 of 40 PASS not_null_raw_customer_regions_region .............................. [PASS in 0.06s]
20:02:25  8 of 40 PASS not_null_raw_customer_regions_customer_id ......................... [PASS in 0.05s]
20:02:25  6 of 40 OK created sql view model main.stg_payments ............................ [OK in 0.08s]
20:02:25  12 of 40 START test unique_stg_payments_payment_id ............................. [RUN]
20:02:25  10 of 40 START test accepted_values_stg_payments_payment_method__credit_card__coupon__bank_transfer__gift_card  [RUN]
20:02:25  11 of 40 START test not_null_stg_payments_payment_id ........................... [RUN]
20:02:25  5 of 40 OK created sql view model main.stg_customers ........................... [OK in 0.12s]
20:02:25  14 of 40 START test unique_stg_customers_customer_id ........................... [RUN]
20:02:25  13 of 40 START test not_null_stg_customers_customer_id ......................... [RUN]
20:02:25  7 of 40 OK created sql view model main.stg_orders .............................. [OK in 0.11s]
20:02:25  15 of 40 START test accepted_values_stg_orders_status__placed__shipped__completed__return_pending__returned__Placed__Shipped__Completed__Return_pending__Returned  [RUN]
20:02:25  16 of 40 START test not_null_stg_orders_order_id ............................... [RUN]
20:02:25  17 of 40 START test unique_stg_orders_order_id ................................. [RUN]
20:02:25  12 of 40 PASS unique_stg_payments_payment_id ................................... [PASS in 0.10s]
20:02:25  10 of 40 PASS accepted_values_stg_payments_payment_method__credit_card__coupon__bank_transfer__gift_card  [PASS in 0.10s]
20:02:25  11 of 40 PASS not_null_stg_payments_payment_id ................................. [PASS in 0.10s]
20:02:25  14 of 40 PASS unique_stg_customers_customer_id ................................. [PASS in 0.07s]
20:02:25  16 of 40 PASS not_null_stg_orders_order_id ..................................... [PASS in 0.06s]
20:02:25  13 of 40 PASS not_null_stg_customers_customer_id ............................... [PASS in 0.07s]
20:02:25  17 of 40 PASS unique_stg_orders_order_id ....................................... [PASS in 0.07s]
20:02:25  15 of 40 PASS accepted_values_stg_orders_status__placed__shipped__completed__return_pending__returned__Placed__Shipped__Completed__Return_pending__Returned  [PASS in 0.07s]
20:02:25  18 of 40 START sql table model main.customers .................................. [RUN]
20:02:25  20 of 40 START sql table model main.region_revenue ............................. [RUN]
20:02:25  19 of 40 START sql table model main.orders ..................................... [RUN]
20:02:26  19 of 40 OK created sql table model main.orders ................................ [OK in 0.07s]
20:02:26  20 of 40 OK created sql table model main.region_revenue ........................ [OK in 0.07s]
20:02:26  18 of 40 OK created sql table model main.customers ............................. [OK in 0.07s]
20:02:26  21 of 40 START test not_null_region_revenue_region ............................. [RUN]
20:02:26  25 of 40 START test unique_customers_customer_id ............................... [RUN]
20:02:26  23 of 40 START test unique_region_revenue_region ............................... [RUN]
20:02:26  22 of 40 START test not_null_region_revenue_revenue ............................ [RUN]
20:02:26  34 of 40 START test relationships_orders_customer_id__customer_id__ref_customers_  [RUN]
20:02:26  24 of 40 START test not_null_customers_customer_id ............................. [RUN]
20:02:26  26 of 40 START test accepted_values_orders_status__placed__shipped__completed__return_pending__returned__Placed__Shipped__Completed__Return_pending__Returned  [RUN]
20:02:26  27 of 40 START test not_null_orders_amount ..................................... [RUN]
20:02:26  28 of 40 START test not_null_orders_bank_transfer_amount ....................... [RUN]
20:02:26  29 of 40 START test not_null_orders_coupon_amount .............................. [RUN]
20:02:26  30 of 40 START test not_null_orders_credit_card_amount ......................... [RUN]
20:02:26  31 of 40 START test not_null_orders_customer_id ................................ [RUN]
20:02:26  32 of 40 START test not_null_orders_gift_card_amount ........................... [RUN]
20:02:26  35 of 40 START test unique_orders_order_id ..................................... [RUN]
20:02:26  33 of 40 START test not_null_orders_order_id ................................... [RUN]
20:02:26  21 of 40 PASS not_null_region_revenue_region ................................... [PASS in 0.12s]
20:02:26  22 of 40 PASS not_null_region_revenue_revenue .................................. [PASS in 0.13s]
20:02:26  26 of 40 PASS accepted_values_orders_status__placed__shipped__completed__return_pending__returned__Placed__Shipped__Completed__Return_pending__Returned  [PASS in 0.13s]
20:02:26  23 of 40 PASS unique_region_revenue_region ..................................... [PASS in 0.13s]
20:02:26  25 of 40 PASS unique_customers_customer_id ..................................... [PASS in 0.13s]
20:02:26  34 of 40 PASS relationships_orders_customer_id__customer_id__ref_customers_ .... [PASS in 0.13s]
20:02:26  27 of 40 PASS not_null_orders_amount ........................................... [PASS in 0.13s]
20:02:26  29 of 40 PASS not_null_orders_coupon_amount .................................... [PASS in 0.13s]
20:02:26  24 of 40 PASS not_null_customers_customer_id ................................... [PASS in 0.13s]
20:02:26  28 of 40 PASS not_null_orders_bank_transfer_amount ............................. [PASS in 0.13s]
20:02:26  31 of 40 PASS not_null_orders_customer_id ...................................... [PASS in 0.13s]
20:02:26  30 of 40 PASS not_null_orders_credit_card_amount ............................... [PASS in 0.13s]
20:02:26  35 of 40 PASS unique_orders_order_id ........................................... [PASS in 0.13s]
20:02:26  33 of 40 PASS not_null_orders_order_id ......................................... [PASS in 0.13s]
20:02:26  32 of 40 PASS not_null_orders_gift_card_amount ................................. [PASS in 0.13s]
20:02:26  36 of 40 START sql incremental model main.daily_revenue ........................ [RUN]
20:02:26  36 of 40 OK created sql incremental model main.daily_revenue ................... [OK in 0.05s]
20:02:26  38 of 40 START test not_null_daily_revenue_orders .............................. [RUN]
20:02:26  37 of 40 START test not_null_daily_revenue_order_date .......................... [RUN]
20:02:26  40 of 40 START test unique_daily_revenue_order_date ............................ [RUN]
20:02:26  39 of 40 START test not_null_daily_revenue_revenue ............................. [RUN]
20:02:26  37 of 40 PASS not_null_daily_revenue_order_date ................................ [PASS in 0.03s]
20:02:26  38 of 40 PASS not_null_daily_revenue_orders .................................... [PASS in 0.04s]
20:02:26  39 of 40 PASS not_null_daily_revenue_revenue ................................... [PASS in 0.03s]
20:02:26  40 of 40 PASS unique_daily_revenue_order_date .................................. [PASS in 0.03s]
20:02:26
20:02:26  Finished running 1 incremental model, 4 seeds, 3 table models, 29 data tests, 3 view models in 0 hours 0 minutes and 0.72 seconds (0.72s).
20:02:26
20:02:26  Completed successfully
20:02:26
20:02:26  Done. PASS=40 WARN=0 ERROR=0 SKIP=0 NO-OP=0 REUSED=0 TOTAL=40

===== output sample =====
```sql
select count(*) as orders, sum(amount) as total_amount from orders
```
```
┌────────┬──────────────┐
│ orders │ total_amount │
│ int64  │    double    │
├────────┼──────────────┤
│     99 │       1672.0 │
└────────┴──────────────┘

```

```sql
select sum(customer_lifetime_value) as lifetime_value, sum(completed_lifetime_value) as completed_lifetime_value from customers
```
```
┌────────────────┬──────────────────────────┐
│ lifetime_value │ completed_lifetime_value │
│     double     │          double          │
├────────────────┼──────────────────────────┤
│         1672.0 │                    604.0 │
└────────────────┴──────────────────────────┘

```

```sql
select status, count(*) as orders, sum(amount) as amount from orders group by status order by status
```
```
┌────────────────┬────────┬────────┐
│     status     │ orders │ amount │
│    varchar     │ int64  │ double │
├────────────────┼────────┼────────┤
│ Completed      │     31 │  499.0 │
│ Placed         │     13 │  284.0 │
│ Return_pending │      1 │   15.0 │
│ Shipped        │     13 │  198.0 │
│ completed      │     36 │  604.0 │
│ return_pending │      1 │   23.0 │
│ returned       │      4 │   49.0 │
└────────────────┴────────┴────────┘

```

```sql
select strftime(order_date, '%Y-%m') as month, status, count(*) as orders from orders group by 1, 2 order by 1, 2
```
```
┌─────────┬────────────────┬────────┐
│  month  │     status     │ orders │
│ varchar │    varchar     │ int64  │
├─────────┼────────────────┼────────┤
│ 2018-01 │ completed      │     24 │
│ 2018-01 │ return_pending │      1 │
│ 2018-01 │ returned       │      4 │
│ 2018-02 │ Completed      │     14 │
│ 2018-02 │ Return_pending │      1 │
│ 2018-02 │ completed      │     12 │
│ 2018-03 │ Completed      │     17 │
│ 2018-03 │ Placed         │      5 │
│ 2018-03 │ Shipped        │     13 │
│ 2018-04 │ Placed         │      8 │
└─────────┴────────────────┴────────┘
  10 rows                 3 columns

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
