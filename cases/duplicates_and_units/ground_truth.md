# Ground truth: duplicates_and_units

**What broke:** `cases/duplicates_and_units/inject.py` applies two feed changes to `jaffle_shop/seeds/raw_payments.csv`
in one load. The gift card provider's export switched to dollars: the 12 gift card rows (payments 9, 22, 26, 48, 49,
67, 68, 71, 92, 99, 107, 110, orders 9, 19, 23, 43, 44, 58, 59, 62, 80, 87, 93, 96) go from 2300, 600, 2300, 1800,
1100, 600, 2800, 1400, 300, 3000, 2600, 1700 to 23, 6, 23, 18, 11, 6, 28, 14, 3, 30, 26, 17. A retried batch
re-ingested every 10th payment with a fresh id: rows 114 to 125 copy payments 1, 11, 21, ..., 111 (orders 1, 10, 18,
26, 36, 46, 54, 62, 71, 79, 88, 97), including the gift card payment 71 in its dollar form (row 121 for order 62, 14).

**Where it shows:** `stg_payments` divides every amount by 100, so the gift cards become 0.23, 0.06, ... and
`orders.gift_card_amount` sums to 2.19 instead of 205.00 (the 12 shrunken rows plus the shrunken copy); the 12 copies
add 109.00 of credit cards and 57.00 of bank transfers (980.00 and 468.00 instead of 871.00 and 411.00). The two
nearly cancel in the total: `orders.amount` is 1635.19 instead of 1672.00 (518.52, 421.29, 584.95, 110.43 by month
against 496.00, 415.00, 622.00, 139.00). Order 1 shows 20.00 for 10.00, order 10 52.00 for 26.00, order 9 0.23 for
23.00, order 62 0.28 for 14.00 (copied and shrunken). `customers` and `region_revenue` follow (1635.19); `daily_revenue`
rebuilds 6 and 7 April only (1669.17 in total).

**Why nothing fails:** every dbt test still passes (44/44): the copies have fresh ids, the gift card rows keep theirs,
and nothing checks a magnitude or a natural key.

**How the output changes:**

| Measure | Clean | Broken |
|---|---|---|
| `sum(amount)` from `orders` | 1672.0 | 1635.19 |
| `sum` of credit_card, coupon, bank_transfer, gift_card columns | 871.0, 185.0, 411.0, 205.0 | 980.0, 185.0, 468.0, 2.19 |
| `sum(amount)` by month in `orders` | 496.0, 415.0, 622.0, 139.0 | 518.52, 421.29, 584.95, 110.43 |
| `orders.amount` for orders 1, 9, 10, 19, 62 | 10.0, 23.0, 26.0, 6.0, 14.0 | 20.0, 0.23, 52.0, 0.06, 0.28 |
| `customer_lifetime_value` for customers 1, 7, 11, 53, 64 | 33.0, 26.0, 3.0, 39.0, 30.0 | 43.0, 52.0, 0.03, 16.23, 38.0 |
| rows in `stg_payments` by method | 55, 13, 33, 12 | 62, 13, 37, 13 |
| `sum(revenue)` from `region_revenue`, from `daily_revenue` | 1672.0, 1672.0 | 1635.19, 1669.17 |

**Key number:** `select round(sum(amount), 2) from orders` is 1672.0 clean and 1635.19 broken (checked by `check_case.py`).

**Correct fix:** both changes, or the output stays wrong: remove the 12 replayed rows (ids 114 to 125; a staging dedupe
on `order_id`, `payment_method`, `amount` removes the same 12, there is no legitimate identical pair) and restore cents
on the 12 gift card rows (times 100 at the source, or have the provider send cents; the replayed gift card row goes
with the duplicates). The gift cards alone leave `orders.amount` at 1852.00; the duplicates alone leave it at 1469.05.
Then `dbt build --full-refresh`. Guards: a uniqueness test on (`order_id`, `payment_method`, `amount`) in
`stg_payments`, a sub-dollar amount check, and a reconciliation of the four method columns to the statements.
