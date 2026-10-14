# Ground truth: late_payments

**What broke:** `cases/late_payments/inject.py` appends 8 rows to `jaffle_shop/seeds/raw_payments.csv`: bank transfers
of 5.00, 12.00, 8.00, 15.00, 6.00, 9.00, 11.00 and 4.00 (70.00, ids 114 to 121) for the February orders 30, 33, 36,
39, 42, 45, 48 and 53, each of which already had one payment. It mimics a second instalment that settles weeks after
the order and lands in the April load.

**Where it shows:** `orders.amount` for those eight orders rises (13.00 to 18.00, 22.00 to 34.00, 9.00 to 17.00, 8.00
to 23.00, 17.00 to 23.00, 5.00 to 14.00, 3.00 to 14.00, 3.00 to 7.00), `sum(orders.amount)` goes from 1672.00 to
1742.00 and February from 415.00 to 485.00; `customers` and `region_revenue` follow. `daily_revenue` is incremental
and rebuilds only the last three days (6 to 9 April), so February keeps its old revenue: it still says 415.00 for the
month and 1672.00 in total. Eight February days disagree on revenue between `orders` and `daily_revenue` while the
order counts agree on every day.

**Why nothing fails:** every dbt test still passes (40/40): the payment ids are fresh, the orders exist, and nothing
compares `daily_revenue` to `orders`.

**How the output changes:**

| Measure | Clean | Broken |
|---|---|---|
| `sum(amount)` from `orders` | 1672.0 | 1742.0 |
| `sum(revenue)`, `sum(orders)` from `daily_revenue` | 1672.0, 99 | 1672.0, 99 |
| February `sum(amount)` from `orders` and `sum(revenue)` from `daily_revenue` | 415.0, 415.0 | 485.0, 415.0 |
| February days where `orders` and `daily_revenue` disagree on revenue | 0 | 8 (2, 4, 10, 11, 16, 17, 21, 26 February) |
| `orders.amount` for order 30, 33 | 13.0, 22.0 | 18.0, 34.0 |
| `sum(customer_lifetime_value)` from `customers`, `sum(revenue)` from `region_revenue` | 1672.0, 1672.0 | 1742.0, 1742.0 |
| `count(*)` from `raw_payments` | 113 | 121 |

**Key number:** `select round((select sum(amount) from orders) - (select sum(revenue) from daily_revenue), 2)` is 0.0 clean and 70.0 broken (checked by `check_case.py`).

**Correct fix:** the payments are real, so the fix is on the dashboard side: rebuild `daily_revenue` in full
(`dbt build --full-refresh --select daily_revenue`), after which it matches `orders` (1742.00, February 485.00),
and change the model so late payments rebuild their day (a load timestamp on payments and a lookback on it, or a
rebuild of every day that received a payment since the last run) instead of a fixed three-day window. Guards: a
singular test that reconciles `daily_revenue` to `orders` by day.
