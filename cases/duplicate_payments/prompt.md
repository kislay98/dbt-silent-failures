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
        sum(amount) as total_amount

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
        customer_payments.total_amount as customer_lifetime_value

    from customers

    left join customer_orders
        on customers.customer_id = customer_orders.customer_id

    left join customer_payments
        on  customers.customer_id = customer_payments.customer_id

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
        description: Date (UTC) that the order was placed

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
        order_date,
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
id,user_id,order_date,status
1,1,2018-01-01,returned
2,3,2018-01-02,completed
3,94,2018-01-04,completed
4,50,2018-01-05,completed
5,64,2018-01-05,completed
6,54,2018-01-07,completed
7,88,2018-01-09,completed
8,2,2018-01-11,returned
9,53,2018-01-12,completed
10,7,2018-01-14,completed
11,99,2018-01-14,completed
12,59,2018-01-15,completed
13,84,2018-01-17,completed
14,40,2018-01-17,returned
15,25,2018-01-17,completed
16,39,2018-01-18,completed
17,71,2018-01-18,completed
18,64,2018-01-20,returned
19,54,2018-01-22,completed
20,20,2018-01-23,completed
21,71,2018-01-23,completed
22,86,2018-01-24,completed
23,22,2018-01-26,return_pending
24,3,2018-01-27,completed
25,51,2018-01-28,completed
26,32,2018-01-28,completed
27,94,2018-01-29,completed
28,8,2018-01-29,completed
29,57,2018-01-31,completed
30,69,2018-02-02,completed
31,16,2018-02-02,completed
32,28,2018-02-04,completed
33,42,2018-02-04,completed
34,38,2018-02-06,completed
35,80,2018-02-08,completed
36,85,2018-02-10,completed
37,1,2018-02-10,completed
38,51,2018-02-10,completed
39,26,2018-02-11,completed
40,33,2018-02-13,completed
41,99,2018-02-14,completed
42,92,2018-02-16,completed
43,31,2018-02-17,completed
44,66,2018-02-17,completed
45,22,2018-02-17,completed
46,6,2018-02-19,completed
47,50,2018-02-20,completed
48,27,2018-02-21,completed
49,35,2018-02-21,completed
50,51,2018-02-23,completed
51,71,2018-02-24,completed
52,54,2018-02-25,return_pending
53,34,2018-02-26,completed
54,54,2018-02-26,completed
55,18,2018-02-27,completed
56,79,2018-02-28,completed
57,93,2018-03-01,completed
58,22,2018-03-01,completed
59,30,2018-03-02,completed
60,12,2018-03-03,completed
61,63,2018-03-03,completed
62,57,2018-03-05,completed
63,70,2018-03-06,completed
64,13,2018-03-07,completed
65,26,2018-03-08,completed
66,36,2018-03-10,completed
67,79,2018-03-11,completed
68,53,2018-03-11,completed
69,3,2018-03-11,completed
70,8,2018-03-12,completed
71,42,2018-03-12,shipped
72,30,2018-03-14,shipped
73,19,2018-03-16,completed
74,9,2018-03-17,shipped
75,69,2018-03-18,completed
76,25,2018-03-20,completed
77,35,2018-03-21,shipped
78,90,2018-03-23,shipped
79,52,2018-03-23,shipped
80,11,2018-03-23,shipped
81,76,2018-03-23,shipped
82,46,2018-03-24,shipped
83,54,2018-03-24,shipped
84,70,2018-03-26,placed
85,47,2018-03-26,shipped
86,68,2018-03-26,placed
87,46,2018-03-27,placed
88,91,2018-03-27,shipped
89,21,2018-03-28,placed
90,66,2018-03-30,shipped
91,47,2018-03-31,placed
92,84,2018-04-02,placed
93,66,2018-04-03,placed
94,63,2018-04-03,placed
95,27,2018-04-04,placed
96,90,2018-04-06,placed
97,89,2018-04-07,placed
98,41,2018-04-07,placed
99,85,2018-04-09,placed

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
20:48:48  Running with dbt=1.12.5
20:48:48  Registered adapter: duckdb=1.11.0
20:48:49  Found 5 models, 3 seeds, 20 data tests, 500 macros
20:48:49  
20:48:49  Concurrency: 24 threads (target='dev')
20:48:49  
20:48:49  1 of 28 START seed file main.raw_customers ..................................... [RUN]
20:48:49  3 of 28 START seed file main.raw_payments ...................................... [RUN]
20:48:49  2 of 28 START seed file main.raw_orders ........................................ [RUN]
20:48:49  1 of 28 OK loaded seed file main.raw_customers ................................. [INSERT 100 in 0.06s]
20:48:49  2 of 28 OK loaded seed file main.raw_orders .................................... [INSERT 99 in 0.05s]
20:48:49  3 of 28 OK loaded seed file main.raw_payments .................................. [INSERT 125 in 0.05s]
20:48:49  4 of 28 START sql view model main.stg_customers ................................ [RUN]
20:48:49  5 of 28 START sql view model main.stg_payments ................................. [RUN]
20:48:49  6 of 28 START sql view model main.stg_orders ................................... [RUN]
20:48:49  4 of 28 OK created sql view model main.stg_customers ........................... [OK in 0.05s]
20:48:49  8 of 28 START test unique_stg_customers_customer_id ............................ [RUN]
20:48:49  7 of 28 START test not_null_stg_customers_customer_id .......................... [RUN]
20:48:49  5 of 28 OK created sql view model main.stg_payments ............................ [OK in 0.05s]
20:48:49  11 of 28 START test unique_stg_payments_payment_id ............................. [RUN]
20:48:49  9 of 28 START test accepted_values_stg_payments_payment_method__credit_card__coupon__bank_transfer__gift_card  [RUN]
20:48:49  10 of 28 START test not_null_stg_payments_payment_id ........................... [RUN]
20:48:49  6 of 28 OK created sql view model main.stg_orders .............................. [OK in 0.09s]
20:48:49  13 of 28 START test not_null_stg_orders_order_id ............................... [RUN]
20:48:49  14 of 28 START test unique_stg_orders_order_id ................................. [RUN]
20:48:49  12 of 28 START test accepted_values_stg_orders_status__placed__shipped__completed__return_pending__returned  [RUN]
20:48:49  8 of 28 PASS unique_stg_customers_customer_id .................................. [PASS in 0.09s]
20:48:49  7 of 28 PASS not_null_stg_customers_customer_id ................................ [PASS in 0.08s]
20:48:49  10 of 28 PASS not_null_stg_payments_payment_id ................................. [PASS in 0.07s]
20:48:49  9 of 28 PASS accepted_values_stg_payments_payment_method__credit_card__coupon__bank_transfer__gift_card  [PASS in 0.07s]
20:48:49  11 of 28 PASS unique_stg_payments_payment_id ................................... [PASS in 0.07s]
20:48:49  13 of 28 PASS not_null_stg_orders_order_id ..................................... [PASS in 0.05s]
20:48:49  14 of 28 PASS unique_stg_orders_order_id ....................................... [PASS in 0.05s]
20:48:49  12 of 28 PASS accepted_values_stg_orders_status__placed__shipped__completed__return_pending__returned  [PASS in 0.05s]
20:48:49  15 of 28 START sql table model main.customers .................................. [RUN]
20:48:49  16 of 28 START sql table model main.orders ..................................... [RUN]
20:48:49  16 of 28 OK created sql table model main.orders ................................ [OK in 0.04s]
20:48:49  15 of 28 OK created sql table model main.customers ............................. [OK in 0.04s]
20:48:49  17 of 28 START test not_null_customers_customer_id ............................. [RUN]
20:48:49  18 of 28 START test unique_customers_customer_id ............................... [RUN]
20:48:49  28 of 28 START test unique_orders_order_id ..................................... [RUN]
20:48:49  19 of 28 START test accepted_values_orders_status__placed__shipped__completed__return_pending__returned  [RUN]
20:48:49  20 of 28 START test not_null_orders_amount ..................................... [RUN]
20:48:49  21 of 28 START test not_null_orders_bank_transfer_amount ....................... [RUN]
20:48:49  22 of 28 START test not_null_orders_coupon_amount .............................. [RUN]
20:48:49  23 of 28 START test not_null_orders_credit_card_amount ......................... [RUN]
20:48:49  24 of 28 START test not_null_orders_customer_id ................................ [RUN]
20:48:49  25 of 28 START test not_null_orders_gift_card_amount ........................... [RUN]
20:48:49  26 of 28 START test not_null_orders_order_id ................................... [RUN]
20:48:49  27 of 28 START test relationships_orders_customer_id__customer_id__ref_customers_  [RUN]
20:48:49  17 of 28 PASS not_null_customers_customer_id ................................... [PASS in 0.08s]
20:48:49  21 of 28 PASS not_null_orders_bank_transfer_amount ............................. [PASS in 0.09s]
20:48:49  18 of 28 PASS unique_customers_customer_id ..................................... [PASS in 0.09s]
20:48:49  19 of 28 PASS accepted_values_orders_status__placed__shipped__completed__return_pending__returned  [PASS in 0.09s]
20:48:49  20 of 28 PASS not_null_orders_amount ........................................... [PASS in 0.09s]
20:48:49  22 of 28 PASS not_null_orders_coupon_amount .................................... [PASS in 0.09s]
20:48:49  25 of 28 PASS not_null_orders_gift_card_amount ................................. [PASS in 0.09s]
20:48:49  23 of 28 PASS not_null_orders_credit_card_amount ............................... [PASS in 0.09s]
20:48:49  28 of 28 PASS unique_orders_order_id ........................................... [PASS in 0.10s]
20:48:49  26 of 28 PASS not_null_orders_order_id ......................................... [PASS in 0.09s]
20:48:49  24 of 28 PASS not_null_orders_customer_id ...................................... [PASS in 0.09s]
20:48:49  27 of 28 PASS relationships_orders_customer_id__customer_id__ref_customers_ .... [PASS in 0.10s]
20:48:49  
20:48:49  Finished running 3 seeds, 2 table models, 20 data tests, 3 view models in 0 hours 0 minutes and 0.49 seconds (0.49s).
20:48:49  
20:48:49  Completed successfully
20:48:49  
20:48:49  Done. PASS=28 WARN=0 ERROR=0 SKIP=0 NO-OP=0 REUSED=0 TOTAL=28

===== output sample =====
```sql
select count(*) as orders, sum(amount) as total_amount from orders
```
```
┌────────┬──────────────┐
│ orders │ total_amount │
│ int64  │    double    │
├────────┼──────────────┤
│     99 │       1852.0 │
└────────┴──────────────┘

```

```sql
select order_id, customer_id, order_date, status, credit_card_amount, coupon_amount, bank_transfer_amount, gift_card_amount, amount from orders where order_id <= 12 order by order_id
```
```
┌──────────┬─────────────┬────────────┬───┬───────────────┬──────────────────────┬──────────────────┬────────┐
│ order_id │ customer_id │ order_date │ … │ coupon_amount │ bank_transfer_amount │ gift_card_amount │ amount │
│  int32   │    int32    │    date    │ … │    double     │        double        │      double      │ double │
├──────────┼─────────────┼────────────┼───┼───────────────┼──────────────────────┼──────────────────┼────────┤
│        1 │           1 │ 2018-01-01 │ … │           0.0 │                  0.0 │              0.0 │   20.0 │
│        2 │           3 │ 2018-01-02 │ … │           0.0 │                  0.0 │              0.0 │   20.0 │
│        3 │          94 │ 2018-01-04 │ … │           1.0 │                  0.0 │              0.0 │    1.0 │
│        4 │          50 │ 2018-01-05 │ … │          25.0 │                  0.0 │              0.0 │   25.0 │
│        5 │          64 │ 2018-01-05 │ … │           0.0 │                 17.0 │              0.0 │   17.0 │
│        6 │          54 │ 2018-01-07 │ … │           0.0 │                  0.0 │              0.0 │    6.0 │
│        7 │          88 │ 2018-01-09 │ … │           0.0 │                  0.0 │              0.0 │   16.0 │
│        8 │           2 │ 2018-01-11 │ … │           0.0 │                  0.0 │              0.0 │   23.0 │
│        9 │          53 │ 2018-01-12 │ … │           0.0 │                  0.0 │             23.0 │   23.0 │
│       10 │           7 │ 2018-01-14 │ … │           0.0 │                 52.0 │              0.0 │   52.0 │
│       11 │          99 │ 2018-01-14 │ … │           0.0 │                  0.0 │              0.0 │   27.0 │
│       12 │          59 │ 2018-01-15 │ … │           0.0 │                  0.0 │              0.0 │    1.0 │
└──────────┴─────────────┴────────────┴───┴───────────────┴──────────────────────┴──────────────────┴────────┘
  12 rows                                                                                9 columns (7 shown)

```

```sql
select * from customers where customer_id in (1,3,7,64,99) order by customer_id
```
```
┌─────────────┬────────────┬───────────┬─────────────┬───────────────────┬──────────────────┬─────────────────────────┐
│ customer_id │ first_name │ last_name │ first_order │ most_recent_order │ number_of_orders │ customer_lifetime_value │
│    int32    │  varchar   │  varchar  │    date     │       date        │      int64       │         double          │
├─────────────┼────────────┼───────────┼─────────────┼───────────────────┼──────────────────┼─────────────────────────┤
│           1 │ Michael    │ P.        │ 2018-01-01  │ 2018-02-10        │                2 │                    43.0 │
│           3 │ Kathleen   │ P.        │ 2018-01-02  │ 2018-03-11        │                3 │                    65.0 │
│           7 │ Martin     │ M.        │ 2018-01-14  │ 2018-01-14        │                1 │                    52.0 │
│          64 │ David      │ C.        │ 2018-01-05  │ 2018-01-20        │                2 │                    38.0 │
│          99 │ Mary       │ G.        │ 2018-01-14  │ 2018-02-14        │                2 │                    44.0 │
└─────────────┴────────────┴───────────┴─────────────┴───────────────────┴──────────────────┴─────────────────────────┘

```

