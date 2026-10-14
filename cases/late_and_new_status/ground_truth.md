# Ground truth: late_and_new_status

**What broke:** `cases/late_and_new_status/inject.py` puts two things into the April load. Six February orders and
their payments arrive a month late with fresh ids (orders 100 to 105 for customers 2, 15, 28, 41, 60 and 77, 3 to
26 February, 65.00 in all, as in `late_orders`), and from 10 March the new fulfilment flow writes `delivered` where
the app used to write `completed`: orders 66 to 76 that were completed (8 orders, 136.00) and the late six, which
the new flow also wrote as `delivered`. The `accepted_values` tests failed on the first run and the on-call added
`delivered` to the lists in `models/schema.yml` and `models/staging/schema.yml`.

**Where it shows:** `orders`, `customers` and `region_revenue` read the seeds and pick up the late orders (105
orders, 1737.00, February 480.00). `daily_revenue` is incremental and rebuilds the last three days only, so the six
February days never get them (99 orders, 1672.00, February 415.00). `customers.completed_lifetime_value` filters on
`status = 'completed'`, so all 14 delivered orders drop out: 967.00 instead of the 1168.00 the admin shows (1103.00
plus the late 65.00), March at 143.00 instead of 279.00. Customer 36's completed revenue falls from 28.00 to 0,
customer 2's late order counts in `customer_lifetime_value` (35.00) but not in completed revenue.

**Why nothing fails:** every dbt test still passes (44/44): the late rows have fresh ids, the widened
`accepted_values` lists accept `delivered`, and nothing reconciles `daily_revenue` to `orders` or completed revenue
to the admin.

**How the output changes:**

| Measure | Clean | Broken | Correct |
|---|---|---|---|
| `count(*)`, `sum(amount)` from `orders` | 99, 1672.0 | 105, 1737.0 | 105, 1737.0 |
| February in `orders` / in `daily_revenue` | 27, 415.0 / 27, 415.0 | 33, 480.0 / 27, 415.0 | 33, 480.0 / 33, 480.0 |
| `sum(amount)` from `orders` minus `sum(revenue)` from `daily_revenue` | 0.0 | 65.0 | 0.0 |
| `sum(completed_lifetime_value)` from `customers` | 1103.0 | 967.0 | 1168.0 |
| `orders` by status | completed 67 (1103.0) | completed 59 (967.0), delivered 14 (201.0) | completed 73 (1168.0) |
| March completed in `orders` | 279.0 | 143.0 | 279.0 |
| `completed_lifetime_value` for customers 2, 3, 36, 53 | 0.0, 65.0, 28.0, 39.0 | 0.0, 46.0, 0.0, 23.0 | 12.0, 65.0, 28.0, 39.0 |
| `accepted_values` lists for status | five values | plus `delivered` | five values, or six with the model reading it |

**Key number:** `select round((select sum(amount) from orders) - (select sum(revenue) from daily_revenue), 2)` is 0.0 clean and 65.0 broken (checked by `check_case.py`).

**Correct fix:** both changes, or the output stays wrong. `daily_revenue` needs a full refresh and a strategy that
survives late rows (rebuild every day present in the new source rows, key the window on a load timestamp, or make it a
table); `delivered` has to count as completed (map it to `completed` in `stg_orders`, or widen the filter in
`customers`), with the `accepted_values` lists matching what the models read. The refresh alone leaves completed
revenue at 967.00; the status alone leaves `daily_revenue` at 1672.00. Restoring the seeds undoes the legitimate
backfill and is wrong. Guards: a per-day reconciliation of `daily_revenue` to `orders`, and a rule that a widened
`accepted_values` list comes with the model change that reads the new value.
