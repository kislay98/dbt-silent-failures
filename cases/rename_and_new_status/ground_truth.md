# Ground truth: rename_and_new_status

**What broke:** `cases/rename_and_new_status/inject.py` applies two changes to `jaffle_shop/seeds/raw_orders.csv` in
one migration. From 1 March 2018 the orders feed writes `order_status` and leaves `status` empty (`order_status`
backfilled for all 99 rows; `status` blank on the 43 orders 57 to 99, as in `status_renamed`). From 10 March the new
fulfilment flow writes `delivered` where the app used to write `completed`, in the new column only: orders 66, 67,
68, 69, 70, 73, 75 and 76 (136.00). No schema file was widened, because the value never reached a test.

**Where it shows:** `stg_orders` still reads `status`, so it is NULL for every order from 1 March and
`customers.completed_lifetime_value` stops counting them: 824.00 instead of 1103.00, March at 0 instead of 279.00.
Customer 3 falls from 65.00 to 46.00, customer 36 to 0. The `delivered` orders hide behind the NULLs: reading the new
column alone brings completed revenue to 967.00, not 1103.00, and makes both `accepted_values` tests fail on
`delivered`. `orders.amount`, `daily_revenue` and `region_revenue` are unchanged.

**Why nothing fails:** every dbt test still passes (44/44): `accepted_values` ignores NULL, nothing tests `not_null`
on status, and `delivered` sits in a column no model reads.

**How the output changes:**

| Measure | Clean | Broken |
|---|---|---|
| `sum(completed_lifetime_value)` from `customers` | 1103.0 | 824.0 |
| `orders` with NULL `status` | 0 | 43 (ids 57 to 99, 761.0) |
| `orders` by status | completed 67 (1103.0), placed 13, shipped 13, return_pending 2, returned 4 | completed 50 (824.0), return_pending 2, returned 4, NULL 43 |
| `completed_lifetime_value` for customers 3, 8, 19, 36, 53, 79 | 65.0, 45.0, 3.0, 28.0, 39.0, 27.0 | 46.0, 19.0, 0.0, 0.0, 23.0, 4.0 |
| `raw_orders.order_status` on the 43 blank-status rows | column absent | completed 9, delivered 8, placed 13, shipped 13 |
| `sum(amount)` from `orders`, `sum(revenue)` from `daily_revenue` | 1672.0, 1672.0 | unchanged |

**Key number:** `select round(sum(completed_lifetime_value), 2) from customers` is 1103.0 clean and 824.0 broken (checked by `check_case.py`).

**Correct fix:** both changes, or the output stays wrong: read the new column (`coalesce(status, order_status)` in
`stg_orders`, or the feed restored) and count `delivered` as completed (map it to `completed` in staging, or widen the
filter in `customers`; if the value is kept, add it to both `accepted_values` lists or the build goes red). The rename
alone gives 967.00 and a failing test; the status alone changes nothing while `status` is NULL. Guards: `not_null` on
`stg_orders.status`, `accepted_values` kept strict (it catches `delivered` as soon as the column is read), and a
reconciliation of completed revenue to the admin.
