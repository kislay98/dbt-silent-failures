# Ground truth: region_fanout_quiet

**What broke:** `cases/region_fanout_quiet/inject.py` appends 2 rows to `jaffle_shop/seeds/raw_customer_regions.csv`:
the rows of customers 3 (NSW, 65.00 lifetime payments) and 51 (VIC, 99.00), the two biggest customers, re-sent with
`updated_at` 2018-04-02 and the same region. Same mechanism as `region_fanout` with a quarter of the rows, so the seed
grows from 100 to 102 and the build log's seed line barely moves.

**Where it shows:** `models/region_revenue.sql` joins payments to orders and then to the regions seed before it
aggregates by region, so both customers' payments are summed twice: NSW 314.00 to 379.00, VIC 399.00 to 498.00, total
1836.00 against 1672.00 in `orders`. `count(distinct ...)` keeps the customer and order counts right. `orders` and
`customers` never read the regions feed and are unchanged.

**Why nothing fails:** every dbt test still passes (40/40): the regions seed is tested `not_null`, not `unique`;
`region_revenue.region` stays unique; nothing reconciles the region report to orders.

**How the output changes:**

| Measure | Clean | Broken |
|---|---|---|
| `sum(region_revenue.revenue)` | 1672.0 | 1836.0 |
| `region_revenue.revenue` for NSW, VIC | 314.0, 399.0 | 379.0, 498.0 |
| `sum(region_revenue.orders)`, `sum(region_revenue.customers)` | 99, 62 | 99, 62 |
| `count(*)`, `count(distinct customer_id)` from `raw_customer_regions` | 100, 100 | 102, 100 |
| `sum(orders.amount)` | 1672.0 | 1672.0 |

**Key number:** `select round(sum(revenue), 2) from region_revenue` is 1672.0 clean and 1836.0 broken (checked by `check_case.py`).

**Correct fix:** remove the 2 appended rows (`git checkout jaffle_shop/seeds/raw_customer_regions.csv`), or keep one
row per customer (latest `updated_at`) before the join. Guards: a `unique` test on `raw_customer_regions.customer_id`,
or a singular test comparing `sum(region_revenue.revenue)` to `sum(orders.amount)`.
