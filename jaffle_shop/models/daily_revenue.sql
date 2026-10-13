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
