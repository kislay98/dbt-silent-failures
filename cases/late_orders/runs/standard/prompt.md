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

payment_methods as (

    {#-
    Processor and fee rate per payment method, loaded as a seed like the other raw tables
    #}
    select * from {{ ref('raw_payment_methods') }}

),

order_payments as (

    select
        payments.order_id,

        {% for payment_method in payment_methods -%}
        sum(case when payments.payment_method = '{{ payment_method }}' then payments.amount else 0 end) as {{ payment_method }}_amount,
        {% endfor -%}

        sum(payments.amount) as total_amount,
        round(sum(payments.amount * coalesce(payment_methods.fee_rate, 0)), 2) as fees

    from payments

    left join payment_methods
        on payments.payment_method = payment_methods.payment_method

    group by payments.order_id

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

        order_payments.total_amount as amount,
        order_payments.fees

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

    join orders
        on payments.order_id = orders.order_id

    join regions
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

      - name: fees
        description: Processor fees (AUD) on the order's payments, at the fee rate of each payment method
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

  - name: raw_payment_methods
    description: One row per payment method from the processor feed, with its processor and fee rate

    columns:
      - name: payment_method
        tests:
          - not_null

      - name: fee_rate
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
18:26:29  Running with dbt=1.12.5
18:26:30  Registered adapter: duckdb=1.11.0
18:26:31  Found 7 models, 5 seeds, 32 data tests, 500 macros
18:26:31
18:26:31  Concurrency: 24 threads (target='dev')
18:26:31
18:26:31  3 of 44 START seed file main.raw_orders ........................................ [RUN]
18:26:31  5 of 44 START seed file main.raw_payments ...................................... [RUN]
18:26:31  4 of 44 START seed file main.raw_payment_methods ............................... [RUN]
18:26:31  1 of 44 START seed file main.raw_customer_regions .............................. [RUN]
18:26:31  2 of 44 START seed file main.raw_customers ..................................... [RUN]
18:26:31  4 of 44 OK loaded seed file main.raw_payment_methods ........................... [INSERT 4 in 0.07s]
18:26:31  3 of 44 OK loaded seed file main.raw_orders .................................... [INSERT 105 in 0.08s]
18:26:31  1 of 44 OK loaded seed file main.raw_customer_regions .......................... [INSERT 100 in 0.08s]
18:26:31  5 of 44 OK loaded seed file main.raw_payments .................................. [INSERT 119 in 0.06s]
18:26:31  8 of 44 START test not_null_raw_payment_methods_payment_method ................. [RUN]
18:26:31  6 of 44 START sql view model main.stg_orders ................................... [RUN]
18:26:31  10 of 44 START test not_null_raw_customer_regions_region ....................... [RUN]
18:26:31  9 of 44 START test not_null_raw_customer_regions_customer_id ................... [RUN]
18:26:31  7 of 44 START test not_null_raw_payment_methods_fee_rate ....................... [RUN]
18:26:31  11 of 44 START sql view model main.stg_payments ................................ [RUN]
18:26:31  2 of 44 OK loaded seed file main.raw_customers ................................. [INSERT 100 in 0.08s]
18:26:31  12 of 44 START sql view model main.stg_customers ............................... [RUN]
18:26:31  7 of 44 PASS not_null_raw_payment_methods_fee_rate ............................. [PASS in 0.10s]
18:26:31  8 of 44 PASS not_null_raw_payment_methods_payment_method ....................... [PASS in 0.11s]
18:26:31  10 of 44 PASS not_null_raw_customer_regions_region ............................. [PASS in 0.11s]
18:26:31  9 of 44 PASS not_null_raw_customer_regions_customer_id ......................... [PASS in 0.12s]
18:26:31  11 of 44 OK created sql view model main.stg_payments ........................... [OK in 0.14s]
18:26:31  13 of 44 START test accepted_values_stg_payments_payment_method__credit_card__coupon__bank_transfer__gift_card  [RUN]
18:26:31  15 of 44 START test unique_stg_payments_payment_id ............................. [RUN]
18:26:31  14 of 44 START test not_null_stg_payments_payment_id ........................... [RUN]
18:26:31  6 of 44 OK created sql view model main.stg_orders .............................. [OK in 0.15s]
18:26:31  12 of 44 OK created sql view model main.stg_customers .......................... [OK in 0.08s]
18:26:31  17 of 44 START test not_null_stg_orders_order_id ............................... [RUN]
18:26:31  16 of 44 START test accepted_values_stg_orders_status__placed__shipped__completed__return_pending__returned  [RUN]
18:26:31  18 of 44 START test unique_stg_orders_order_id ................................. [RUN]
18:26:31  20 of 44 START test unique_stg_customers_customer_id ........................... [RUN]
18:26:31  19 of 44 START test not_null_stg_customers_customer_id ......................... [RUN]
18:26:31  14 of 44 PASS not_null_stg_payments_payment_id ................................. [PASS in 0.08s]
18:26:31  13 of 44 PASS accepted_values_stg_payments_payment_method__credit_card__coupon__bank_transfer__gift_card  [PASS in 0.09s]
18:26:31  15 of 44 PASS unique_stg_payments_payment_id ................................... [PASS in 0.09s]
18:26:31  17 of 44 PASS not_null_stg_orders_order_id ..................................... [PASS in 0.08s]
18:26:31  20 of 44 PASS unique_stg_customers_customer_id ................................. [PASS in 0.07s]
18:26:31  16 of 44 PASS accepted_values_stg_orders_status__placed__shipped__completed__return_pending__returned  [PASS in 0.07s]
18:26:31  18 of 44 PASS unique_stg_orders_order_id ....................................... [PASS in 0.07s]
18:26:31  19 of 44 PASS not_null_stg_customers_customer_id ............................... [PASS in 0.07s]
18:26:31  21 of 44 START sql table model main.customers .................................. [RUN]
18:26:31  23 of 44 START sql table model main.region_revenue ............................. [RUN]
18:26:31  22 of 44 START sql table model main.orders ..................................... [RUN]
18:26:31  22 of 44 OK created sql table model main.orders ................................ [OK in 0.07s]
18:26:31  24 of 44 START test accepted_values_orders_status__placed__shipped__completed__return_pending__returned  [RUN]
18:26:31  25 of 44 START test not_null_orders_amount ..................................... [RUN]
18:26:31  26 of 44 START test not_null_orders_bank_transfer_amount ....................... [RUN]
18:26:31  27 of 44 START test not_null_orders_coupon_amount .............................. [RUN]
18:26:31  28 of 44 START test not_null_orders_credit_card_amount ......................... [RUN]
18:26:31  29 of 44 START test not_null_orders_customer_id ................................ [RUN]
18:26:31  32 of 44 START test not_null_orders_order_id ................................... [RUN]
18:26:31  30 of 44 START test not_null_orders_fees ....................................... [RUN]
18:26:31  31 of 44 START test not_null_orders_gift_card_amount ........................... [RUN]
18:26:31  33 of 44 START test unique_orders_order_id ..................................... [RUN]
18:26:31  21 of 44 OK created sql table model main.customers ............................. [OK in 0.10s]
18:26:31  23 of 44 OK created sql table model main.region_revenue ........................ [OK in 0.09s]
18:26:31  37 of 44 START test relationships_orders_customer_id__customer_id__ref_customers_  [RUN]
18:26:31  34 of 44 START test not_null_customers_customer_id ............................. [RUN]
18:26:31  35 of 44 START test not_null_region_revenue_region ............................. [RUN]
18:26:31  36 of 44 START test not_null_region_revenue_revenue ............................ [RUN]
18:26:31  38 of 44 START test unique_customers_customer_id ............................... [RUN]
18:26:31  39 of 44 START test unique_region_revenue_region ............................... [RUN]
18:26:31  24 of 44 PASS accepted_values_orders_status__placed__shipped__completed__return_pending__returned  [PASS in 0.14s]
18:26:31  26 of 44 PASS not_null_orders_bank_transfer_amount ............................. [PASS in 0.14s]
18:26:31  29 of 44 PASS not_null_orders_customer_id ...................................... [PASS in 0.14s]
18:26:31  28 of 44 PASS not_null_orders_credit_card_amount ............................... [PASS in 0.14s]
18:26:31  25 of 44 PASS not_null_orders_amount ........................................... [PASS in 0.14s]
18:26:31  32 of 44 PASS not_null_orders_order_id ......................................... [PASS in 0.14s]
18:26:31  31 of 44 PASS not_null_orders_gift_card_amount ................................. [PASS in 0.14s]
18:26:31  27 of 44 PASS not_null_orders_coupon_amount .................................... [PASS in 0.15s]
18:26:31  30 of 44 PASS not_null_orders_fees ............................................. [PASS in 0.14s]
18:26:31  33 of 44 PASS unique_orders_order_id ........................................... [PASS in 0.15s]
18:26:31  40 of 44 START sql incremental model main.daily_revenue ........................ [RUN]
18:26:31  37 of 44 PASS relationships_orders_customer_id__customer_id__ref_customers_ .... [PASS in 0.09s]
18:26:31  34 of 44 PASS not_null_customers_customer_id ................................... [PASS in 0.09s]
18:26:31  35 of 44 PASS not_null_region_revenue_region ................................... [PASS in 0.09s]
18:26:31  38 of 44 PASS unique_customers_customer_id ..................................... [PASS in 0.09s]
18:26:31  36 of 44 PASS not_null_region_revenue_revenue .................................. [PASS in 0.09s]
18:26:31  39 of 44 PASS unique_region_revenue_region ..................................... [PASS in 0.09s]
18:26:32  40 of 44 OK created sql incremental model main.daily_revenue ................... [OK in 0.06s]
18:26:32  41 of 44 START test not_null_daily_revenue_order_date .......................... [RUN]
18:26:32  43 of 44 START test not_null_daily_revenue_revenue ............................. [RUN]
18:26:32  44 of 44 START test unique_daily_revenue_order_date ............................ [RUN]
18:26:32  42 of 44 START test not_null_daily_revenue_orders .............................. [RUN]
18:26:32  43 of 44 PASS not_null_daily_revenue_revenue ................................... [PASS in 0.02s]
18:26:32  44 of 44 PASS unique_daily_revenue_order_date .................................. [PASS in 0.04s]
18:26:32  42 of 44 PASS not_null_daily_revenue_orders .................................... [PASS in 0.04s]
18:26:32  41 of 44 PASS not_null_daily_revenue_order_date ................................ [PASS in 0.04s]
18:26:32
18:26:32  Finished running 1 incremental model, 5 seeds, 3 table models, 32 data tests, 3 view models in 0 hours 0 minutes and 0.86 seconds (0.86s).
18:26:32
18:26:32  Completed successfully
18:26:32
18:26:32  Done. PASS=44 WARN=0 ERROR=0 SKIP=0 NO-OP=0 REUSED=0 TOTAL=44

===== output sample =====
```sql
select 'customers' as "table", count(*) as rows from customers union all select 'daily_revenue' as "table", count(*) as rows from daily_revenue union all select 'orders' as "table", count(*) as rows from orders union all select 'raw_customer_regions' as "table", count(*) as rows from raw_customer_regions union all select 'raw_customers' as "table", count(*) as rows from raw_customers union all select 'raw_orders' as "table", count(*) as rows from raw_orders union all select 'raw_payment_methods' as "table", count(*) as rows from raw_payment_methods union all select 'raw_payments' as "table", count(*) as rows from raw_payments union all select 'region_revenue' as "table", count(*) as rows from region_revenue union all select 'stg_customers' as "table", count(*) as rows from stg_customers union all select 'stg_orders' as "table", count(*) as rows from stg_orders union all select 'stg_payments' as "table", count(*) as rows from stg_payments
```
```
┌──────────────────────┬───────┐
│        table         │  rows │
│       varchar        │ int64 │
├──────────────────────┼───────┤
│ customers            │   100 │
│ daily_revenue        │    69 │
│ orders               │   105 │
│ raw_customer_regions │   100 │
│ raw_customers        │   100 │
│ raw_orders           │   105 │
│ raw_payment_methods  │     4 │
│ raw_payments         │   119 │
│ region_revenue       │     7 │
│ stg_customers        │   100 │
│ stg_orders           │   105 │
│ stg_payments         │   119 │
└──────────────────────┴───────┘
  12 rows

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
│ raw_payment_methods  │ payment_method │ VARCHAR   │
│ raw_payment_methods  │ processor      │ VARCHAR   │
│ raw_payment_methods  │ fee_rate       │ DOUBLE    │
│ raw_payments         │ id             │ INTEGER   │
│ raw_payments         │ order_id       │ INTEGER   │
│ raw_payments         │ payment_method │ VARCHAR   │
│ raw_payments         │ amount         │ INTEGER   │
└──────────────────────┴────────────────┴───────────┘
  17 rows

```

```sql
select date_trunc('month', order_date) as month, count(*) as orders, round(sum(amount), 2) as amount, round(sum(credit_card_amount), 2) as credit_card, round(sum(coupon_amount), 2) as coupon, round(sum(bank_transfer_amount), 2) as bank_transfer, round(sum(gift_card_amount), 2) as gift_card, round(sum(fees), 2) as fees from orders group by 1 order by 1 nulls last
```
```
┌─────────────────────┬────────┬────────┬─────────────┬────────┬───────────────┬───────────┬────────┐
│        month        │ orders │ amount │ credit_card │ coupon │ bank_transfer │ gift_card │  fees  │
│      timestamp      │ int64  │ double │    double   │ double │     double    │   double  │ double │
├─────────────────────┼────────┼────────┼─────────────┼────────┼───────────────┼───────────┼────────┤
│ 2018-01-01 00:00:00 │     29 │  496.0 │       218.0 │   90.0 │         136.0 │      52.0 │   7.03 │
│ 2018-02-01 00:00:00 │     33 │  480.0 │       342.0 │   23.0 │          86.0 │      29.0 │  10.42 │
│ 2018-03-01 00:00:00 │     35 │  622.0 │       334.0 │   45.0 │         162.0 │      81.0 │   10.5 │
│ 2018-04-01 00:00:00 │      8 │  139.0 │        24.0 │   33.0 │          39.0 │      43.0 │    0.9 │
└─────────────────────┴────────┴────────┴─────────────┴────────┴───────────────┴───────────┴────────┘

```

```sql
select status, count(*) as orders, round(sum(amount), 2) as amount from orders group by 1 order by 1 nulls last
```
```
┌────────────────┬────────┬────────┐
│     status     │ orders │ amount │
│    varchar     │ int64  │ double │
├────────────────┼────────┼────────┤
│ completed      │     73 │ 1168.0 │
│ placed         │     13 │  284.0 │
│ return_pending │      2 │   38.0 │
│ returned       │      4 │   49.0 │
│ shipped        │     13 │  198.0 │
└────────────────┴────────┴────────┘

```

```sql
select date_trunc('month', order_date) as month, sum(orders) as orders, round(sum(revenue), 2) as revenue from daily_revenue group by 1 order by 1 nulls last
```
```
┌─────────────────────┬────────┬─────────┐
│        month        │ orders │ revenue │
│      timestamp      │ int128 │  double │
├─────────────────────┼────────┼─────────┤
│ 2018-01-01 00:00:00 │     29 │   496.0 │
│ 2018-02-01 00:00:00 │     27 │   415.0 │
│ 2018-03-01 00:00:00 │     35 │   622.0 │
│ 2018-04-01 00:00:00 │      8 │   139.0 │
└─────────────────────┴────────┴─────────┘

```

```sql
select count(*) as customers, count(first_order) as with_orders, sum(number_of_orders) as orders, round(sum(customer_lifetime_value), 2) as lifetime_value, round(sum(completed_lifetime_value), 2) as completed_lifetime_value from customers
```
```
┌───────────┬─────────────┬────────┬────────────────┬──────────────────────────┐
│ customers │ with_orders │ orders │ lifetime_value │ completed_lifetime_value │
│   int64   │    int64    │ int128 │     double     │          double          │
├───────────┼─────────────┼────────┼────────────────┼──────────────────────────┤
│       100 │          65 │    105 │         1737.0 │                   1168.0 │
└───────────┴─────────────┴────────┴────────────────┴──────────────────────────┘

```

```sql
select region, customers, orders, round(revenue, 2) as revenue from region_revenue order by 1
```
```
┌─────────┬───────────┬────────┬─────────┐
│  region │ customers │ orders │ revenue │
│ varchar │   int64   │ int64  │  double │
├─────────┼───────────┼────────┼─────────┤
│ ACT     │         4 │      5 │    75.0 │
│ NSW     │        14 │     23 │   349.0 │
│ QLD     │        11 │     22 │   356.0 │
│ SA      │         4 │      6 │   105.0 │
│ TAS     │         7 │      9 │   130.0 │
│ VIC     │        12 │     22 │   411.0 │
│ WA      │        13 │     18 │   311.0 │
└─────────┴───────────┴────────┴─────────┘

```

```sql
select * from customers t order by hash(t) limit 10
```
```
┌─────────────┬────────────┬───────────┬─────────────┬───────────────────┬──────────────────┬─────────────────────────┬──────────────────────────┐
│ customer_id │ first_name │ last_name │ first_order │ most_recent_order │ number_of_orders │ customer_lifetime_value │ completed_lifetime_value │
│    int32    │  varchar   │  varchar  │     date    │        date       │      int64       │          double         │          double          │
├─────────────┼────────────┼───────────┼─────────────┼───────────────────┼──────────────────┼─────────────────────────┼──────────────────────────┤
│          70 │ Helen      │ F.        │ 2018-03-06  │ 2018-03-26        │                2 │                    54.0 │                     29.0 │
│           3 │ Kathleen   │ P.        │ 2018-01-02  │ 2018-03-11        │                3 │                    65.0 │                     65.0 │
│          52 │ Laura      │ F.        │ 2018-03-23  │ 2018-03-23        │                1 │                    27.0 │                      0.0 │
│          21 │ Willie     │ H.        │ 2018-03-28  │ 2018-03-28        │                1 │                    22.0 │                      0.0 │
│           8 │ Frank      │ R.        │ 2018-01-29  │ 2018-03-12        │                2 │                    45.0 │                     45.0 │
│          64 │ David      │ C.        │ 2018-01-05  │ 2018-01-20        │                2 │                    30.0 │                     17.0 │
│          57 │ Paul       │ W.        │ 2018-01-31  │ 2018-03-05        │                2 │                    26.0 │                     26.0 │
│          55 │ Nicholas   │ R.        │ NULL        │ NULL              │             NULL │                    NULL │                     NULL │
│          45 │ Scott      │ B.        │ NULL        │ NULL              │             NULL │                    NULL │                     NULL │
│          14 │ Steve      │ F.        │ NULL        │ NULL              │             NULL │                    NULL │                     NULL │
└─────────────┴────────────┴───────────┴─────────────┴───────────────────┴──────────────────┴─────────────────────────┴──────────────────────────┘

```

```sql
select * from daily_revenue t order by hash(t) limit 10
```
```
┌────────────┬────────┬─────────┐
│ order_date │ orders │ revenue │
│    date    │ int64  │  double │
├────────────┼────────┼─────────┤
│ 2018-04-03 │      2 │    33.0 │
│ 2018-01-29 │      2 │    42.0 │
│ 2018-02-24 │      1 │    30.0 │
│ 2018-01-20 │      1 │    13.0 │
│ 2018-01-23 │      2 │    27.0 │
│ 2018-01-27 │      1 │    26.0 │
│ 2018-02-08 │      1 │    29.0 │
│ 2018-01-04 │      1 │     1.0 │
│ 2018-03-07 │      1 │    26.0 │
│ 2018-03-03 │      2 │    20.0 │
└────────────┴────────┴─────────┘

```

```sql
select * from orders t order by hash(t) limit 10
```
```
┌──────────┬─────────────┬────────────┬───────────┬────────────────────┬───────────────┬──────────────────────┬──────────────────┬────────┬────────┐
│ order_id │ customer_id │ order_date │   status  │ credit_card_amount │ coupon_amount │ bank_transfer_amount │ gift_card_amount │ amount │  fees  │
│  int32   │    int32    │    date    │  varchar  │       double       │     double    │        double        │      double      │ double │ double │
├──────────┼─────────────┼────────────┼───────────┼────────────────────┼───────────────┼──────────────────────┼──────────────────┼────────┼────────┤
│       25 │          51 │ 2018-01-28 │ completed │               22.0 │          16.0 │                 20.0 │              0.0 │   58.0 │   0.74 │
│       96 │          90 │ 2018-04-06 │ placed    │                0.0 │           0.0 │                  0.0 │             17.0 │   17.0 │    0.0 │
│        6 │          54 │ 2018-01-07 │ completed │                6.0 │           0.0 │                  0.0 │              0.0 │    6.0 │   0.17 │
│       70 │           8 │ 2018-03-12 │ completed │               26.0 │           0.0 │                  0.0 │              0.0 │   26.0 │   0.75 │
│       87 │          46 │ 2018-03-27 │ placed    │               26.0 │           0.0 │                  0.0 │             30.0 │   56.0 │   0.75 │
│        7 │          88 │ 2018-01-09 │ completed │               16.0 │           0.0 │                  0.0 │              0.0 │   16.0 │   0.46 │
│      103 │          41 │ 2018-02-17 │ completed │                0.0 │           6.0 │                  0.0 │              0.0 │    6.0 │    0.0 │
│      105 │          77 │ 2018-02-26 │ completed │                0.0 │           0.0 │                  3.0 │              0.0 │    3.0 │   0.02 │
│       85 │          47 │ 2018-03-26 │ shipped   │                0.0 │           0.0 │                 17.0 │              0.0 │   17.0 │   0.09 │
│       57 │          93 │ 2018-03-01 │ completed │                0.0 │           0.0 │                  2.0 │              0.0 │    2.0 │   0.01 │
└──────────┴─────────────┴────────────┴───────────┴────────────────────┴───────────────┴──────────────────────┴──────────────────┴────────┴────────┘

```

```sql
select * from raw_customer_regions t order by hash(t) limit 10
```
```
┌─────────────┬─────────┬────────────┐
│ customer_id │  region │ updated_at │
│    int32    │ varchar │    date    │
├─────────────┼─────────┼────────────┤
│          22 │ VIC     │ 2017-11-07 │
│          73 │ NSW     │ 2017-12-21 │
│          98 │ NSW     │ 2017-12-01 │
│           1 │ ACT     │ 2017-11-13 │
│          81 │ NSW     │ 2017-11-07 │
│          20 │ WA      │ 2017-11-05 │
│           7 │ TAS     │ 2017-11-28 │
│           6 │ ACT     │ 2017-12-25 │
│          83 │ VIC     │ 2017-12-13 │
│          15 │ NSW     │ 2017-11-13 │
└─────────────┴─────────┴────────────┘

```

```sql
select * from raw_customers t order by hash(t) limit 10
```
```
┌───────┬────────────┬───────────┐
│   id  │ first_name │ last_name │
│ int32 │  varchar   │  varchar  │
├───────┼────────────┼───────────┤
│    51 │ Howard     │ R.        │
│     9 │ Jennifer   │ F.        │
│     5 │ Katherine  │ R.        │
│     7 │ Martin     │ M.        │
│    13 │ Kathleen   │ M.        │
│    80 │ Phillip    │ H.        │
│    45 │ Scott      │ B.        │
│    34 │ Jennifer   │ S.        │
│   100 │ Jean       │ M.        │
│     4 │ Jimmy      │ C.        │
└───────┴────────────┴───────────┘

```

```sql
select * from raw_orders t order by hash(t) limit 10
```
```
┌───────┬─────────┬─────────────────────┬───────────┐
│   id  │ user_id │      ordered_at     │   status  │
│ int32 │  int32  │      timestamp      │  varchar  │
├───────┼─────────┼─────────────────────┼───────────┤
│    84 │      70 │ 2018-03-26 12:34:27 │ placed    │
│    16 │      39 │ 2018-01-18 02:37:25 │ completed │
│   102 │      28 │ 2018-02-13 13:05:51 │ completed │
│    98 │      41 │ 2018-04-07 20:35:56 │ placed    │
│    88 │      91 │ 2018-03-27 03:21:20 │ shipped   │
│    13 │      84 │ 2018-01-17 10:30:20 │ completed │
│    31 │      16 │ 2018-02-02 13:52:24 │ completed │
│    12 │      59 │ 2018-01-15 15:43:37 │ completed │
│    28 │       8 │ 2018-01-29 22:50:40 │ completed │
│    50 │      51 │ 2018-02-23 18:53:27 │ completed │
└───────┴─────────┴─────────────────────┴───────────┘

```

```sql
select * from raw_payment_methods t order by hash(t) limit 10
```
```
┌────────────────┬───────────┬──────────┐
│ payment_method │ processor │ fee_rate │
│    varchar     │  varchar  │  double  │
├────────────────┼───────────┼──────────┤
│ bank_transfer  │ anz       │    0.005 │
│ coupon         │ internal  │      0.0 │
│ credit_card    │ stripe    │    0.029 │
│ gift_card      │ internal  │      0.0 │
└────────────────┴───────────┴──────────┘

```

```sql
select * from raw_payments t order by hash(t) limit 10
```
```
┌───────┬──────────┬────────────────┬────────┐
│   id  │ order_id │ payment_method │ amount │
│ int32 │  int32   │    varchar     │ int32  │
├───────┼──────────┼────────────────┼────────┤
│    28 │       25 │ bank_transfer  │   2000 │
│   103 │       90 │ bank_transfer  │    200 │
│    21 │       18 │ credit_card    │    800 │
│    14 │       13 │ credit_card    │    500 │
│    76 │       67 │ bank_transfer  │    400 │
│    18 │       16 │ credit_card    │   1000 │
│    65 │       57 │ bank_transfer  │    200 │
│    27 │       24 │ coupon         │   2600 │
│    58 │       51 │ credit_card    │    100 │
│    59 │       52 │ bank_transfer  │   1500 │
└───────┴──────────┴────────────────┴────────┘

```

```sql
select * from region_revenue t order by hash(t) limit 10
```
```
┌─────────┬───────────┬────────┬─────────┐
│  region │ customers │ orders │ revenue │
│ varchar │   int64   │ int64  │  double │
├─────────┼───────────┼────────┼─────────┤
│ ACT     │         4 │      5 │    75.0 │
│ VIC     │        12 │     22 │   411.0 │
│ NSW     │        14 │     23 │   349.0 │
│ QLD     │        11 │     22 │   356.0 │
│ TAS     │         7 │      9 │   130.0 │
│ SA      │         4 │      6 │   105.0 │
│ WA      │        13 │     18 │   311.0 │
└─────────┴───────────┴────────┴─────────┘

```

```sql
select * from stg_customers t order by hash(t) limit 10
```
```
┌─────────────┬────────────┬───────────┐
│ customer_id │ first_name │ last_name │
│    int32    │  varchar   │  varchar  │
├─────────────┼────────────┼───────────┤
│          51 │ Howard     │ R.        │
│           9 │ Jennifer   │ F.        │
│           5 │ Katherine  │ R.        │
│           7 │ Martin     │ M.        │
│          13 │ Kathleen   │ M.        │
│          80 │ Phillip    │ H.        │
│          45 │ Scott      │ B.        │
│          34 │ Jennifer   │ S.        │
│         100 │ Jean       │ M.        │
│           4 │ Jimmy      │ C.        │
└─────────────┴────────────┴───────────┘

```

```sql
select * from stg_orders t order by hash(t) limit 10
```
```
┌──────────┬─────────────┬─────────────────────┬────────────┬───────────┐
│ order_id │ customer_id │      ordered_at     │ order_date │   status  │
│  int32   │    int32    │      timestamp      │    date    │  varchar  │
├──────────┼─────────────┼─────────────────────┼────────────┼───────────┤
│        2 │           3 │ 2018-01-02 14:40:42 │ 2018-01-02 │ completed │
│        7 │          88 │ 2018-01-09 15:40:33 │ 2018-01-09 │ completed │
│       14 │          40 │ 2018-01-17 15:18:18 │ 2018-01-17 │ returned  │
│       39 │          26 │ 2018-02-11 17:41:11 │ 2018-02-11 │ completed │
│       68 │          53 │ 2018-03-11 10:58:27 │ 2018-03-11 │ completed │
│       41 │          99 │ 2018-02-14 10:44:12 │ 2018-02-14 │ completed │
│       25 │          51 │ 2018-01-28 09:00:52 │ 2018-01-28 │ completed │
│       63 │          70 │ 2018-03-06 17:58:52 │ 2018-03-06 │ completed │
│       20 │          20 │ 2018-01-23 04:16:41 │ 2018-01-23 │ completed │
│       57 │          93 │ 2018-03-01 12:18:40 │ 2018-03-01 │ completed │
└──────────┴─────────────┴─────────────────────┴────────────┴───────────┘

```

```sql
select * from stg_payments t order by hash(t) limit 10
```
```
┌────────────┬──────────┬────────────────┬────────┐
│ payment_id │ order_id │ payment_method │ amount │
│   int32    │  int32   │    varchar     │ double │
├────────────┼──────────┼────────────────┼────────┤
│         33 │       28 │ bank_transfer  │   19.0 │
│         32 │       27 │ credit_card    │   23.0 │
│        104 │       91 │ credit_card    │   19.0 │
│         55 │       49 │ credit_card    │    9.0 │
│          3 │        3 │ coupon         │    1.0 │
│        118 │      104 │ credit_card    │   20.0 │
│         16 │       14 │ bank_transfer  │    3.0 │
│         85 │       75 │ credit_card    │   19.0 │
│        114 │      100 │ credit_card    │   12.0 │
│         59 │       52 │ bank_transfer  │   15.0 │
└────────────┴──────────┴────────────────┴────────┘

```
