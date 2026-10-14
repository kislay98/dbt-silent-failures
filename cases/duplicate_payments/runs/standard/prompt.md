You are debugging a dbt project (dbt-core 1.12, dbt-duckdb adapter). `dbt build` completes with every test passing,
but finance says the numbers are wrong: the processor settlement total for these orders is 1672.00, while
`sum(orders.amount)` is 1852.00. Order 1 shows amount 20.00 but the customer was charged 10.00.

Below are the full project files (models, schema tests, seeds), the `dbt build` log, and a sample of the output tables.
Answer with:
1. The root cause, naming the exact file(s) and rows.
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

===== jaffle_shop/seeds/raw_customer_regions.csv =====
customer_id,region,updated_at
1,ACT,2017-11-13
2,VIC,2017-12-12
3,NSW,2017-11-10
4,NSW,2017-12-12
5,NSW,2017-12-03
6,ACT,2017-12-25
7,TAS,2017-11-28
8,VIC,2017-11-20
9,WA,2017-12-28
10,WA,2017-12-19
11,WA,2017-11-09
12,WA,2017-12-04
13,QLD,2017-12-18
14,VIC,2017-11-26
15,NSW,2017-11-13
16,NSW,2017-12-25
17,TAS,2017-12-19
18,VIC,2017-11-15
19,TAS,2017-11-03
20,WA,2017-11-05
21,WA,2017-12-15
22,VIC,2017-11-07
23,VIC,2017-11-28
24,NSW,2017-11-18
25,TAS,2017-12-25
26,VIC,2017-12-20
27,QLD,2017-12-10
28,QLD,2017-12-25
29,VIC,2017-11-27
30,VIC,2017-11-01
31,ACT,2017-12-11
32,VIC,2017-12-19
33,TAS,2017-12-12
34,VIC,2017-12-09
35,WA,2017-11-10
36,TAS,2017-12-02
37,QLD,2017-12-07
38,NSW,2017-12-28
39,SA,2017-12-27
40,VIC,2017-12-06
41,NSW,2017-11-24
42,TAS,2017-12-25
43,VIC,2017-11-22
44,QLD,2017-11-09
45,NSW,2017-12-27
46,QLD,2017-12-25
47,QLD,2017-12-26
48,SA,2017-12-09
49,QLD,2017-11-06
50,NSW,2017-11-10
51,VIC,2017-12-12
52,NSW,2017-11-26
53,SA,2017-12-27
54,QLD,2017-12-08
55,VIC,2017-12-19
56,VIC,2017-12-16
57,VIC,2017-11-11
58,WA,2017-11-05
59,WA,2017-12-23
60,NSW,2017-11-07
61,QLD,2017-11-27
62,VIC,2017-12-01
63,QLD,2017-12-23
64,NSW,2017-11-04
65,NSW,2017-12-05
66,NSW,2017-11-14
67,NSW,2017-11-24
68,QLD,2017-12-22
69,NSW,2017-11-04
70,WA,2017-11-27
71,WA,2017-11-25
72,NSW,2017-11-21
73,NSW,2017-12-21
74,VIC,2017-12-24
75,VIC,2017-11-23
76,NSW,2017-12-21
77,QLD,2017-11-10
78,SA,2017-12-10
79,SA,2017-11-02
80,WA,2017-11-14
81,NSW,2017-11-07
82,NSW,2017-11-02
83,VIC,2017-12-13
84,QLD,2017-12-11
85,NSW,2017-12-17
86,TAS,2017-12-14
87,WA,2017-12-22
88,ACT,2017-12-25
89,WA,2017-12-11
90,QLD,2017-11-07
91,SA,2017-12-27
92,WA,2017-12-11
93,NSW,2017-12-25
94,VIC,2017-11-07
95,WA,2017-12-12
96,ACT,2017-12-17
97,TAS,2017-11-13
98,NSW,2017-12-01
99,WA,2017-12-24
100,NSW,2017-11-24

===== jaffle_shop/seeds/raw_customers.csv =====
id,first_name,last_name
1,Michael,P.
2,Shawn,M.
3,Kathleen,P.
4,Jimmy,C.
5,Katherine,R.
6,Sarah,R.
7,Martin,M.
8,Frank,R.
9,Jennifer,F.
10,Henry,W.
11,Fred,S.
12,Amy,D.
13,Kathleen,M.
14,Steve,F.
15,Teresa,H.
16,Amanda,H.
17,Kimberly,R.
18,Johnny,K.
19,Virginia,F.
20,Anna,A.
21,Willie,H.
22,Sean,H.
23,Mildred,A.
24,David,G.
25,Victor,H.
26,Aaron,R.
27,Benjamin,B.
28,Lisa,W.
29,Benjamin,K.
30,Christina,W.
31,Jane,G.
32,Thomas,O.
33,Katherine,M.
34,Jennifer,S.
35,Sara,T.
36,Harold,O.
37,Shirley,J.
38,Dennis,J.
39,Louise,W.
40,Maria,A.
41,Gloria,C.
42,Diana,S.
43,Kelly,N.
44,Jane,R.
45,Scott,B.
46,Norma,C.
47,Marie,P.
48,Lillian,C.
49,Judy,N.
50,Billy,L.
51,Howard,R.
52,Laura,F.
53,Anne,B.
54,Rose,M.
55,Nicholas,R.
56,Joshua,K.
57,Paul,W.
58,Kathryn,K.
59,Adam,A.
60,Norma,W.
61,Timothy,R.
62,Elizabeth,P.
63,Edward,G.
64,David,C.
65,Brenda,W.
66,Adam,W.
67,Michael,H.
68,Jesse,E.
69,Janet,P.
70,Helen,F.
71,Gerald,C.
72,Kathryn,O.
73,Alan,B.
74,Harry,A.
75,Andrea,H.
76,Barbara,W.
77,Anne,W.
78,Harry,H.
79,Jack,R.
80,Phillip,H.
81,Shirley,H.
82,Arthur,D.
83,Virginia,R.
84,Christina,R.
85,Theresa,M.
86,Jason,C.
87,Phillip,B.
88,Adam,T.
89,Margaret,J.
90,Paul,P.
91,Todd,W.
92,Willie,O.
93,Frances,R.
94,Gregory,H.
95,Lisa,P.
96,Jacqueline,A.
97,Shirley,D.
98,Nicole,M.
99,Mary,G.
100,Jean,M.

===== jaffle_shop/seeds/raw_orders.csv =====
id,user_id,ordered_at,status
1,1,2018-01-01 17:08:01,returned
2,3,2018-01-02 14:40:42,completed
3,94,2018-01-04 16:19:07,completed
4,50,2018-01-05 11:15:02,completed
5,64,2018-01-05 15:34:35,completed
6,54,2018-01-07 02:55:04,completed
7,88,2018-01-09 15:40:33,completed
8,2,2018-01-11 02:29:49,returned
9,53,2018-01-12 19:38:49,completed
10,7,2018-01-14 01:05:47,completed
11,99,2018-01-14 09:14:12,completed
12,59,2018-01-15 15:43:37,completed
13,84,2018-01-17 10:30:20,completed
14,40,2018-01-17 15:18:18,returned
15,25,2018-01-17 19:21:39,completed
16,39,2018-01-18 02:37:25,completed
17,71,2018-01-18 09:59:14,completed
18,64,2018-01-20 13:20:54,returned
19,54,2018-01-22 19:56:45,completed
20,20,2018-01-23 04:16:41,completed
21,71,2018-01-23 15:21:16,completed
22,86,2018-01-24 10:54:07,completed
23,22,2018-01-26 06:09:32,return_pending
24,3,2018-01-27 20:41:32,completed
25,51,2018-01-28 09:00:52,completed
26,32,2018-01-28 11:00:22,completed
27,94,2018-01-29 19:44:02,completed
28,8,2018-01-29 22:50:40,completed
29,57,2018-01-31 20:54:14,completed
30,69,2018-02-02 13:04:45,completed
31,16,2018-02-02 13:52:24,completed
32,28,2018-02-04 14:21:29,completed
33,42,2018-02-04 18:13:15,completed
34,38,2018-02-06 11:01:02,completed
35,80,2018-02-08 05:14:18,completed
36,85,2018-02-10 12:17:11,completed
37,1,2018-02-10 13:52:04,completed
38,51,2018-02-10 17:42:36,completed
39,26,2018-02-11 17:41:11,completed
40,33,2018-02-13 11:07:47,completed
41,99,2018-02-14 10:44:12,completed
42,92,2018-02-16 19:23:15,completed
43,31,2018-02-17 03:50:26,completed
44,66,2018-02-17 05:47:55,completed
45,22,2018-02-17 12:02:24,completed
46,6,2018-02-19 13:09:25,completed
47,50,2018-02-20 16:21:19,completed
48,27,2018-02-21 15:56:19,completed
49,35,2018-02-21 23:01:31,completed
50,51,2018-02-23 18:53:27,completed
51,71,2018-02-24 16:10:42,completed
52,54,2018-02-25 16:38:48,return_pending
53,34,2018-02-26 07:20:17,completed
54,54,2018-02-26 11:11:56,completed
55,18,2018-02-27 17:00:23,completed
56,79,2018-02-28 10:39:12,completed
57,93,2018-03-01 12:18:40,completed
58,22,2018-03-01 22:43:06,completed
59,30,2018-03-02 14:45:45,completed
60,12,2018-03-03 12:24:08,completed
61,63,2018-03-03 15:09:12,completed
62,57,2018-03-05 19:24:13,completed
63,70,2018-03-06 17:58:52,completed
64,13,2018-03-07 08:58:55,completed
65,26,2018-03-08 19:36:22,completed
66,36,2018-03-10 16:02:29,completed
67,79,2018-03-11 09:10:20,completed
68,53,2018-03-11 10:58:27,completed
69,3,2018-03-11 11:45:34,completed
70,8,2018-03-12 13:30:27,completed
71,42,2018-03-12 20:16:39,shipped
72,30,2018-03-14 13:25:15,shipped
73,19,2018-03-16 01:15:20,completed
74,9,2018-03-17 15:37:38,shipped
75,69,2018-03-18 07:01:48,completed
76,25,2018-03-20 16:38:48,completed
77,35,2018-03-21 18:55:03,shipped
78,90,2018-03-23 12:57:20,shipped
79,52,2018-03-23 14:23:44,shipped
80,11,2018-03-23 14:26:26,shipped
81,76,2018-03-23 21:23:11,shipped
82,46,2018-03-24 05:14:48,shipped
83,54,2018-03-24 07:05:32,shipped
84,70,2018-03-26 12:34:27,placed
85,47,2018-03-26 17:20:12,shipped
86,68,2018-03-26 21:15:38,placed
87,46,2018-03-27 02:42:29,placed
88,91,2018-03-27 03:21:20,shipped
89,21,2018-03-28 00:15:59,placed
90,66,2018-03-30 14:41:49,shipped
91,47,2018-03-31 16:46:28,placed
92,84,2018-04-02 02:25:29,placed
93,66,2018-04-03 02:56:01,placed
94,63,2018-04-03 13:05:18,placed
95,27,2018-04-04 10:46:47,placed
96,90,2018-04-06 18:46:25,placed
97,89,2018-04-07 13:16:59,placed
98,41,2018-04-07 20:35:56,placed
99,85,2018-04-09 23:35:08,placed

===== jaffle_shop/seeds/raw_payment_methods.csv =====
payment_method,processor,fee_rate
credit_card,stripe,0.029
coupon,internal,0
bank_transfer,anz,0.005
gift_card,internal,0

===== jaffle_shop/seeds/raw_payments.csv =====
id,order_id,payment_method,amount
1,1,credit_card,1000
2,2,credit_card,2000
3,3,coupon,100
4,4,coupon,2500
5,5,bank_transfer,1700
6,6,credit_card,600
7,7,credit_card,1600
8,8,credit_card,2300
9,9,gift_card,2300
10,9,bank_transfer,0
11,10,bank_transfer,2600
12,11,credit_card,2700
13,12,credit_card,100
14,13,credit_card,500
15,13,bank_transfer,1400
16,14,bank_transfer,300
17,15,coupon,2200
18,16,credit_card,1000
19,17,bank_transfer,200
20,18,credit_card,500
21,18,credit_card,800
22,19,gift_card,600
23,20,bank_transfer,1500
24,21,credit_card,1200
25,22,bank_transfer,800
26,23,gift_card,2300
27,24,coupon,2600
28,25,bank_transfer,2000
29,25,credit_card,2200
30,25,coupon,1600
31,26,credit_card,3000
32,27,credit_card,2300
33,28,bank_transfer,1900
34,29,bank_transfer,1200
35,30,credit_card,1300
36,31,credit_card,1200
37,32,credit_card,300
38,33,credit_card,2200
39,34,bank_transfer,1500
40,35,credit_card,2900
41,36,bank_transfer,900
42,37,credit_card,2300
43,38,credit_card,1500
44,39,bank_transfer,800
45,40,credit_card,1400
46,41,credit_card,1700
47,42,coupon,1700
48,43,gift_card,1800
49,44,gift_card,1100
50,45,bank_transfer,500
51,46,bank_transfer,800
52,47,credit_card,2200
53,48,bank_transfer,300
54,49,credit_card,600
55,49,credit_card,900
56,50,credit_card,2600
57,51,credit_card,2900
58,51,credit_card,100
59,52,bank_transfer,1500
60,53,credit_card,300
61,54,credit_card,1800
62,54,bank_transfer,1100
63,55,credit_card,2900
64,56,credit_card,400
65,57,bank_transfer,200
66,58,coupon,1800
67,58,gift_card,600
68,59,gift_card,2800
69,60,credit_card,400
70,61,bank_transfer,1600
71,62,gift_card,1400
72,63,credit_card,2900
73,64,bank_transfer,2600
74,65,credit_card,0
75,66,credit_card,2800
76,67,bank_transfer,400
77,67,credit_card,1900
78,68,credit_card,1600
79,69,credit_card,1900
80,70,credit_card,2600
81,71,credit_card,500
82,72,credit_card,2900
83,73,bank_transfer,300
84,74,credit_card,3000
85,75,credit_card,1900
86,76,coupon,200
87,77,credit_card,0
88,77,bank_transfer,1900
89,78,bank_transfer,2600
90,79,credit_card,1800
91,79,credit_card,900
92,80,gift_card,300
93,81,coupon,200
94,82,credit_card,800
95,83,credit_card,100
96,84,bank_transfer,2500
97,85,bank_transfer,1700
98,86,coupon,2300
99,87,gift_card,3000
100,87,credit_card,2600
101,88,credit_card,2900
102,89,bank_transfer,2200
103,90,bank_transfer,200
104,91,credit_card,1900
105,92,bank_transfer,1500
106,92,coupon,200
107,93,gift_card,2600
108,94,coupon,700
109,95,coupon,2400
110,96,gift_card,1700
111,97,bank_transfer,1400
112,98,bank_transfer,1000
113,99,credit_card,2400
114,1,credit_card,1000
115,10,bank_transfer,2600
116,18,credit_card,800
117,26,credit_card,3000
118,36,bank_transfer,900
119,46,bank_transfer,800
120,54,credit_card,1800
121,62,gift_card,1400
122,71,credit_card,500
123,79,credit_card,900
124,88,credit_card,2900
125,97,bank_transfer,1400

===== dbt build log =====
18:19:06  Running with dbt=1.12.5
18:19:06  Registered adapter: duckdb=1.11.0
18:19:07  Found 7 models, 5 seeds, 32 data tests, 500 macros
18:19:07
18:19:07  Concurrency: 24 threads (target='dev')
18:19:07
18:19:07  2 of 44 START seed file main.raw_customers ..................................... [RUN]
18:19:07  4 of 44 START seed file main.raw_payment_methods ............................... [RUN]
18:19:07  3 of 44 START seed file main.raw_orders ........................................ [RUN]
18:19:07  1 of 44 START seed file main.raw_customer_regions .............................. [RUN]
18:19:07  5 of 44 START seed file main.raw_payments ...................................... [RUN]
18:19:08  4 of 44 OK loaded seed file main.raw_payment_methods ........................... [INSERT 4 in 0.07s]
18:19:08  3 of 44 OK loaded seed file main.raw_orders .................................... [INSERT 99 in 0.08s]
18:19:08  5 of 44 OK loaded seed file main.raw_payments .................................. [INSERT 125 in 0.06s]
18:19:08  6 of 44 START sql view model main.stg_orders ................................... [RUN]
18:19:08  2 of 44 OK loaded seed file main.raw_customers ................................. [INSERT 100 in 0.09s]
18:19:08  1 of 44 OK loaded seed file main.raw_customer_regions .......................... [INSERT 100 in 0.06s]
18:19:08  7 of 44 START test not_null_raw_payment_methods_fee_rate ....................... [RUN]
18:19:08  8 of 44 START test not_null_raw_payment_methods_payment_method ................. [RUN]
18:19:08  9 of 44 START sql view model main.stg_payments ................................. [RUN]
18:19:08  11 of 44 START test not_null_raw_customer_regions_region ....................... [RUN]
18:19:08  10 of 44 START test not_null_raw_customer_regions_customer_id .................. [RUN]
18:19:08  12 of 44 START sql view model main.stg_customers ............................... [RUN]
18:19:08  7 of 44 PASS not_null_raw_payment_methods_fee_rate ............................. [PASS in 0.10s]
18:19:08  8 of 44 PASS not_null_raw_payment_methods_payment_method ....................... [PASS in 0.09s]
18:19:08  10 of 44 PASS not_null_raw_customer_regions_customer_id ........................ [PASS in 0.08s]
18:19:08  11 of 44 PASS not_null_raw_customer_regions_region ............................. [PASS in 0.09s]
18:19:08  9 of 44 OK created sql view model main.stg_payments ............................ [OK in 0.11s]
18:19:08  12 of 44 OK created sql view model main.stg_customers .......................... [OK in 0.10s]
18:19:08  6 of 44 OK created sql view model main.stg_orders .............................. [OK in 0.12s]
18:19:08  13 of 44 START test accepted_values_stg_payments_payment_method__credit_card__coupon__bank_transfer__gift_card  [RUN]
18:19:08  17 of 44 START test unique_stg_payments_payment_id ............................. [RUN]
18:19:08  14 of 44 START test not_null_stg_customers_customer_id ......................... [RUN]
18:19:08  16 of 44 START test unique_stg_customers_customer_id ........................... [RUN]
18:19:08  15 of 44 START test not_null_stg_payments_payment_id ........................... [RUN]
18:19:08  20 of 44 START test unique_stg_orders_order_id ................................. [RUN]
18:19:08  19 of 44 START test not_null_stg_orders_order_id ............................... [RUN]
18:19:08  18 of 44 START test accepted_values_stg_orders_status__placed__shipped__completed__return_pending__returned  [RUN]
18:19:08  13 of 44 PASS accepted_values_stg_payments_payment_method__credit_card__coupon__bank_transfer__gift_card  [PASS in 0.08s]
18:19:08  16 of 44 PASS unique_stg_customers_customer_id ................................. [PASS in 0.08s]
18:19:08  15 of 44 PASS not_null_stg_payments_payment_id ................................. [PASS in 0.08s]
18:19:08  17 of 44 PASS unique_stg_payments_payment_id ................................... [PASS in 0.08s]
18:19:08  14 of 44 PASS not_null_stg_customers_customer_id ............................... [PASS in 0.08s]
18:19:08  20 of 44 PASS unique_stg_orders_order_id ....................................... [PASS in 0.08s]
18:19:08  18 of 44 PASS accepted_values_stg_orders_status__placed__shipped__completed__return_pending__returned  [PASS in 0.07s]
18:19:08  19 of 44 PASS not_null_stg_orders_order_id ..................................... [PASS in 0.07s]
18:19:08  21 of 44 START sql table model main.customers .................................. [RUN]
18:19:08  23 of 44 START sql table model main.region_revenue ............................. [RUN]
18:19:08  22 of 44 START sql table model main.orders ..................................... [RUN]
18:19:08  22 of 44 OK created sql table model main.orders ................................ [OK in 0.07s]
18:19:08  23 of 44 OK created sql table model main.region_revenue ........................ [OK in 0.08s]
18:19:08  31 of 44 START test not_null_orders_gift_card_amount ........................... [RUN]
18:19:08  28 of 44 START test not_null_orders_credit_card_amount ......................... [RUN]
18:19:08  25 of 44 START test not_null_orders_amount ..................................... [RUN]
18:19:08  29 of 44 START test not_null_orders_customer_id ................................ [RUN]
18:19:08  30 of 44 START test not_null_orders_fees ....................................... [RUN]
18:19:08  32 of 44 START test not_null_orders_order_id ................................... [RUN]
18:19:08  26 of 44 START test not_null_orders_bank_transfer_amount ....................... [RUN]
18:19:08  36 of 44 START test unique_region_revenue_region ............................... [RUN]
18:19:08  24 of 44 START test accepted_values_orders_status__placed__shipped__completed__return_pending__returned  [RUN]
18:19:08  27 of 44 START test not_null_orders_coupon_amount .............................. [RUN]
18:19:08  33 of 44 START test unique_orders_order_id ..................................... [RUN]
18:19:08  34 of 44 START test not_null_region_revenue_region ............................. [RUN]
18:19:08  35 of 44 START test not_null_region_revenue_revenue ............................ [RUN]
18:19:08  21 of 44 OK created sql table model main.customers ............................. [OK in 0.10s]
18:19:08  39 of 44 START test unique_customers_customer_id ............................... [RUN]
18:19:08  38 of 44 START test relationships_orders_customer_id__customer_id__ref_customers_  [RUN]
18:19:08  37 of 44 START test not_null_customers_customer_id ............................. [RUN]
18:19:08  28 of 44 PASS not_null_orders_credit_card_amount ............................... [PASS in 0.16s]
18:19:08  31 of 44 PASS not_null_orders_gift_card_amount ................................. [PASS in 0.16s]
18:19:08  29 of 44 PASS not_null_orders_customer_id ...................................... [PASS in 0.15s]
18:19:08  32 of 44 PASS not_null_orders_order_id ......................................... [PASS in 0.16s]
18:19:08  30 of 44 PASS not_null_orders_fees ............................................. [PASS in 0.16s]
18:19:08  26 of 44 PASS not_null_orders_bank_transfer_amount ............................. [PASS in 0.16s]
18:19:08  36 of 44 PASS unique_region_revenue_region ..................................... [PASS in 0.16s]
18:19:08  25 of 44 PASS not_null_orders_amount ........................................... [PASS in 0.16s]
18:19:08  34 of 44 PASS not_null_region_revenue_region ................................... [PASS in 0.16s]
18:19:08  24 of 44 PASS accepted_values_orders_status__placed__shipped__completed__return_pending__returned  [PASS in 0.16s]
18:19:08  33 of 44 PASS unique_orders_order_id ........................................... [PASS in 0.16s]
18:19:08  27 of 44 PASS not_null_orders_coupon_amount .................................... [PASS in 0.16s]
18:19:08  35 of 44 PASS not_null_region_revenue_revenue .................................. [PASS in 0.16s]
18:19:08  39 of 44 PASS unique_customers_customer_id ..................................... [PASS in 0.08s]
18:19:08  40 of 44 START sql incremental model main.daily_revenue ........................ [RUN]
18:19:08  38 of 44 PASS relationships_orders_customer_id__customer_id__ref_customers_ .... [PASS in 0.09s]
18:19:08  37 of 44 PASS not_null_customers_customer_id ................................... [PASS in 0.09s]
18:19:08  40 of 44 OK created sql incremental model main.daily_revenue ................... [OK in 0.05s]
18:19:08  41 of 44 START test not_null_daily_revenue_order_date .......................... [RUN]
18:19:08  44 of 44 START test unique_daily_revenue_order_date ............................ [RUN]
18:19:08  43 of 44 START test not_null_daily_revenue_revenue ............................. [RUN]
18:19:08  42 of 44 START test not_null_daily_revenue_orders .............................. [RUN]
18:19:08  41 of 44 PASS not_null_daily_revenue_order_date ................................ [PASS in 0.03s]
18:19:08  43 of 44 PASS not_null_daily_revenue_revenue ................................... [PASS in 0.02s]
18:19:08  42 of 44 PASS not_null_daily_revenue_orders .................................... [PASS in 0.03s]
18:19:08  44 of 44 PASS unique_daily_revenue_order_date .................................. [PASS in 0.04s]
18:19:08
18:19:08  Finished running 1 incremental model, 5 seeds, 3 table models, 32 data tests, 3 view models in 0 hours 0 minutes and 0.82 seconds (0.82s).
18:19:08
18:19:08  Completed successfully
18:19:08
18:19:08  Done. PASS=44 WARN=0 ERROR=0 SKIP=0 NO-OP=0 REUSED=0 TOTAL=44

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
│ orders               │    99 │
│ raw_customer_regions │   100 │
│ raw_customers        │   100 │
│ raw_orders           │    99 │
│ raw_payment_methods  │     4 │
│ raw_payments         │   125 │
│ region_revenue       │     7 │
│ stg_customers        │   100 │
│ stg_orders           │    99 │
│ stg_payments         │   125 │
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
│ 2018-01-01 00:00:00 │     29 │  570.0 │       266.0 │   90.0 │         162.0 │      52.0 │   8.55 │
│ 2018-02-01 00:00:00 │     27 │  450.0 │       313.0 │   17.0 │          91.0 │      29.0 │   9.58 │
│ 2018-03-01 00:00:00 │     35 │  679.0 │       377.0 │   45.0 │         162.0 │      95.0 │  11.74 │
│ 2018-04-01 00:00:00 │      8 │  153.0 │        24.0 │   33.0 │          53.0 │      43.0 │   0.97 │
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
│ completed      │     67 │ 1208.0 │
│ placed         │     13 │  298.0 │
│ return_pending │      2 │   38.0 │
│ returned       │      4 │   67.0 │
│ shipped        │     13 │  241.0 │
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
│ 2018-04-01 00:00:00 │      8 │   153.0 │
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
│       100 │          62 │     99 │         1852.0 │                   1208.0 │
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
│ ACT     │         4 │      5 │    93.0 │
│ NSW     │        12 │     20 │   340.0 │
│ QLD     │        10 │     20 │   356.0 │
│ SA      │         4 │      6 │   134.0 │
│ TAS     │         7 │      9 │   161.0 │
│ VIC     │        12 │     21 │   443.0 │
│ WA      │        13 │     18 │   325.0 │
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
│          21 │ Willie     │ H.        │ 2018-03-28  │ 2018-03-28        │                1 │                    22.0 │                      0.0 │
│           8 │ Frank      │ R.        │ 2018-01-29  │ 2018-03-12        │                2 │                    45.0 │                     45.0 │
│          55 │ Nicholas   │ R.        │ NULL        │ NULL              │             NULL │                    NULL │                     NULL │
│          45 │ Scott      │ B.        │ NULL        │ NULL              │             NULL │                    NULL │                     NULL │
│          14 │ Steve      │ F.        │ NULL        │ NULL              │             NULL │                    NULL │                     NULL │
│          46 │ Norma      │ C.        │ 2018-03-24  │ 2018-03-27        │                2 │                    64.0 │                      0.0 │
│          71 │ Gerald     │ C.        │ 2018-01-18  │ 2018-02-24        │                3 │                    44.0 │                     44.0 │
│          69 │ Janet      │ P.        │ 2018-02-02  │ 2018-03-18        │                2 │                    32.0 │                     32.0 │
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
│       54 │          54 │ 2018-02-26 │ completed │               36.0 │           0.0 │                 11.0 │              0.0 │   47.0 │    1.1 │
│       70 │           8 │ 2018-03-12 │ completed │               26.0 │           0.0 │                  0.0 │              0.0 │   26.0 │   0.75 │
│       87 │          46 │ 2018-03-27 │ placed    │               26.0 │           0.0 │                  0.0 │             30.0 │   56.0 │   0.75 │
│        7 │          88 │ 2018-01-09 │ completed │               16.0 │           0.0 │                  0.0 │              0.0 │   16.0 │   0.46 │
│       85 │          47 │ 2018-03-26 │ shipped   │                0.0 │           0.0 │                 17.0 │              0.0 │   17.0 │   0.09 │
│       57 │          93 │ 2018-03-01 │ completed │                0.0 │           0.0 │                  2.0 │              0.0 │    2.0 │   0.01 │
│       78 │          90 │ 2018-03-23 │ shipped   │                0.0 │           0.0 │                 26.0 │              0.0 │   26.0 │   0.13 │
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
│    98 │      41 │ 2018-04-07 20:35:56 │ placed    │
│    88 │      91 │ 2018-03-27 03:21:20 │ shipped   │
│    13 │      84 │ 2018-01-17 10:30:20 │ completed │
│    31 │      16 │ 2018-02-02 13:52:24 │ completed │
│    12 │      59 │ 2018-01-15 15:43:37 │ completed │
│    28 │       8 │ 2018-01-29 22:50:40 │ completed │
│    50 │      51 │ 2018-02-23 18:53:27 │ completed │
│    51 │      71 │ 2018-02-24 16:10:42 │ completed │
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
│ VIC     │        12 │     21 │   443.0 │
│ WA      │        13 │     18 │   325.0 │
│ SA      │         4 │      6 │   134.0 │
│ QLD     │        10 │     20 │   356.0 │
│ ACT     │         4 │      5 │    93.0 │
│ TAS     │         7 │      9 │   161.0 │
│ NSW     │        12 │     20 │   340.0 │
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
│         16 │       14 │ bank_transfer  │    3.0 │
│         85 │       75 │ credit_card    │   19.0 │
│         59 │       52 │ bank_transfer  │   15.0 │
│          7 │        7 │ credit_card    │   16.0 │
│         60 │       53 │ credit_card    │    3.0 │
└────────────┴──────────┴────────────────┴────────┘

```
