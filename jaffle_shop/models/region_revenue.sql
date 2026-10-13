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
