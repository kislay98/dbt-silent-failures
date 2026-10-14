# Ground truth: duplicate_payments

**What broke:** `cases/duplicate_payments/inject.py` appends 12 rows to `jaffle_shop/seeds/raw_payments.csv`.
Each is an exact copy of every 10th existing payment (orders 1, 10, 18, 26, 36, 46, 54, 62, 71, 79, 88, 97)
with a fresh `id` (114-125). It mimics a retried batch load that re-ingested rows under new surrogate keys.

**Where it shows:** `stg_payments` has 125 rows instead of 113. `orders.amount` and the per-method columns
are summed from payments in `models/orders.sql`, so the 12 affected orders double their amount.
`customers.customer_lifetime_value` sums `orders.amount`, so the 12 customers who own those orders inflate too.

**Why nothing fails:** every dbt test still passes (28/28). The only tests on payments are `unique`/`not_null`
on `payment_id` (ids are fresh) and `accepted_values` on `payment_method` (copied verbatim).
Nothing checks one payment per order+method, or that amounts reconcile to an external total.

**How the output changes:**

| Measure | Clean | Broken |
|---|---|---|
| `sum(orders.amount)` | 1672.0 | 1852.0 |
| `orders.amount` for order 1 | 10.0 | 20.0 |
| `orders.amount` for order 10 | 26.0 | 52.0 |
| `customers.customer_lifetime_value` for customer 1 | 33.0 | 43.0 |
| `count(*) from stg_payments` | 113 | 125 |

**Key number:** `select round(sum(amount), 2) from orders` is 1672.0 clean and 1852.0 broken (checked by `check_case.py`).

**Correct fix:** remove the 12 appended rows (`git checkout jaffle_shop/seeds/raw_payments.csv`), or dedupe
in `stg_payments` on (`order_id`, `payment_method`, `amount`). A guard that would have caught it: a `unique`
test on a `dbt_utils.generate_surrogate_key(['order_id','payment_method'])`-style column in `stg_payments`,
or a singular test comparing `sum(amount)` to the settlement total.
