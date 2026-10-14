# Ground truth: transfers_in_micros

**What broke:** `cases/transfers_in_micros/inject.py` rewrites the 10 bank transfers on orders placed from 15 March 2018
(payments 83, 88, 89, 96, 97, 102, 103, 105, 111 and 112; orders 73, 77, 78, 84, 85, 89, 90, 92, 97 and 98) in
`jaffle_shop/seeds/raw_payments.csv` from cents to micro-units (one millionth of a dollar, so 10,000 times the cents
value: 300 becomes 3,000,000). The 23 earlier transfers and every other method stay in cents. It mimics a provider
whose v2 API changed units.

**Where it shows:** `stg_payments` divides by 100 as if every row were cents, so those ten transfers come out 10,000
times too large: order 73 shows 30,000.00 instead of 3.00. `sum(orders.bank_transfer_amount)` goes from 411.00 to
1,530,258.00 and `sum(orders.amount)` from 1672.00 to 1,531,519.00; March bank transfers read 1,140,048.00 against
162.00 and April 390,000.00 against 39.00; the first two weeks of March are right. `customers` and `region_revenue`
follow (WA 311.00 to 800,231.00). `daily_revenue` rebuilds 6 to 9 April only, so it picks up the two April 7 orders
(241,648.00 in total) and keeps the old March.

**Why nothing fails:** every dbt test still passes (40/40): ids and methods are untouched, and nothing checks the
magnitude of a payment or reconciles to the processor.

**How the output changes:**

| Measure | Clean | Broken |
|---|---|---|
| `sum(bank_transfer_amount)`, `sum(amount)` from `orders` | 411.0, 1672.0 | 1530258.0, 1531519.0 |
| March `sum(bank_transfer_amount)`, `sum(amount)` | 162.0, 622.0 | 1140048.0, 1140508.0 |
| `orders.amount` for orders 73, 77, 92 | 3.0, 19.0, 17.0 | 30000.0, 190000.0, 150002.0 |
| `region_revenue.revenue` for WA, QLD, NSW, TAS | 311.0, 338.0, 314.0, 130.0 | 800231.0, 580280.0, 120302.0, 30127.0 |
| `raw_payments.amount` for payment 83 | 300 | 3000000 |
| `sum(revenue)` from `daily_revenue` | 1672.0 | 241648.0 |

**Key number:** `select round(sum(bank_transfer_amount), 2) from orders` is 411.0 clean and 1530258.0 broken (checked by `check_case.py`).

**Correct fix:** restore the ten rows (`git checkout jaffle_shop/seeds/raw_payments.csv`), or divide the v2 rows
(bank transfers on orders placed from 15 March, payment id 83 and later) by 10,000 at the source or in staging, and
full-refresh `daily_revenue`. Dividing every bank transfer would shrink the 23 earlier ones. Guards: a test that no
payment exceeds a plausible ceiling, or a reconciliation to the processor statement by method and month.
