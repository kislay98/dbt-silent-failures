# Ground truth: join_drops_rows

**What broke:** `cases/join_drops_rows/inject.py` blanks `order_id` on the last payment of each of the 13 orders that
were paid with more than one payment (payment ids 10, 15, 21, 30, 55, 58, 62, 67, 77, 88, 91, 100, 106; 140.00 in total) in
`jaffle_shop/seeds/raw_payments.csv`. It mimics a gateway release after which the second leg of a split-tender payment
arrives with an empty order reference.

**Where it shows:** `models/orders.sql` groups payments by `order_id`, so the 13 orphan payments fall into a NULL group
that the left join from `orders` never matches: each affected order keeps its first payment and loses the rest, order
9 (-0.00), 13 (-14.00), 18 (-8.00), 25 (-16.00), 49 (-9.00), 51 (-1.00), 54 (-11.00), 58 (-6.00), 67 (-19.00), 77 (-19.00), 79 (-9.00), 87 (-26.00), 92 (-2.00). `models/customers.sql` sums payments through `orders`, so the same 140.00 leaves `customer_lifetime_value`
(and 76.00 of it leaves `completed_lifetime_value`). `models/region_revenue.sql` inner-joins payments to orders, so the
orphans vanish from every region. `stg_payments` still has 113 rows and 1672.00, which is the only table that agrees
with the settlement. `daily_revenue` is stale and happens to keep the old, correct totals.

**Why nothing fails:** every dbt test still passes (40/40). `stg_payments.order_id` has no `not_null` or
`relationships` test; the affected orders all keep at least one payment, so `not_null` on the amount columns holds;
the inner join in `region_revenue` drops the orphans instead of producing a NULL region that `not_null` would catch.

**How the output changes:**

| Measure | Clean | Broken |
|---|---|---|
| `sum(orders.amount)` | 1672.0 | 1532.0 |
| `sum(stg_payments.amount)`, `count(*)` | 1672.0, 113 | 1672.0, 113 |
| payments with no matching order | 0 | 13 (140.0) |
| `sum(region_revenue.revenue)` | 1672.0 | 1532.0 |
| `sum(customers.customer_lifetime_value)`, `sum(completed_lifetime_value)` | 1672.0, 1103.0 | 1532.0, 1027.0 |
| `orders.amount` for order 25 (three payments) | 58.0 | 42.0 |
| orders with 1, 2, 3 matched payments | 86, 12, 1 | 98, 1, 0 |
| `sum(daily_revenue.revenue)` | 1672.0 | 1672.0 (stale) |

**Key number:** `select round(sum(amount), 2) from orders` is 1672.0 clean and 1532.0 broken (checked by `check_case.py`).

**Correct fix:** the order references are gone from the warehouse, so the data has to come back from the source:
restore the seed (`git checkout jaffle_shop/seeds/raw_payments.csv`) or have the gateway re-send the 13 payments with
their order ids. No model change recovers them; a model that drops or re-attributes the orphans is wrong. Guards that
would have caught it: `not_null` and a `relationships` test to `stg_orders` on `stg_payments.order_id`, or a singular
test that `sum(orders.amount)` equals `sum(stg_payments.amount)`.
