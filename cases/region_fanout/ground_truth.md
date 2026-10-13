# Ground truth: region_fanout

**What broke:** `cases/region_fanout/inject.py` appends 8 rows to `jaffle_shop/seeds/raw_customer_regions.csv`: the
existing rows of customers 1, 12, 25, 34, 46, 59, 76 and 90 (every 8th customer with orders), re-sent with
`updated_at` 2018-04-02 and the same region. It mimics a dimension feed that replays changed records and a loader that
appends them instead of upserting on the key.

**Where it shows:** `models/region_revenue.sql` joins payments to orders and then to the regions seed before it
aggregates by region. Each re-sent customer now matches two region rows, so every payment of those 8 customers is
summed twice in `revenue`. The `count(distinct ...)` columns absorb the duplicate rows, so customers and orders per
region stay correct. `orders` and `customers` never read the regions feed, so `sum(orders.amount)` and
`sum(customer_lifetime_value)` still reconcile to 1672.00 while the region report sums to 1846.00.

**Why nothing fails:** every dbt test still passes (40/40). The regions seed is tested `not_null` on `customer_id` and
`region`, not `unique`. `region_revenue.region` stays unique because the `group by` collapses the duplicates, and the
counts and revenue are merely `not_null`. Nothing checks one region row per customer, or that the region report
reconciles to orders.

**How the output changes:**

| Measure | Clean | Broken |
|---|---|---|
| `sum(region_revenue.revenue)` | 1672.0 | 1846.0 |
| `region_revenue.revenue` for QLD | 338.0 | 445.0 |
| `region_revenue.revenue` for ACT | 75.0 | 108.0 |
| `region_revenue.revenue` for TAS | 130.0 | 154.0 |
| `region_revenue.revenue` for SA | 105.0 | 105.0 |
| `sum(region_revenue.orders)`, `sum(region_revenue.customers)` | 99, 62 | 99, 62 |
| `count(*)`, `count(distinct customer_id)` from `raw_customer_regions` | 100, 100 | 108, 100 |
| `sum(orders.amount)` | 1672.0 | 1672.0 |

Doubled customers (region, lifetime payments): 1 (ACT, 33.00), 12 (WA, 4.00), 25 (TAS, 24.00), 34 (VIC, 3.00),
46 (QLD, 64.00), 59 (WA, 1.00), 76 (NSW, 2.00), 90 (QLD, 43.00). Total 174.00 = 1846.00 - 1672.00. Per region:
ACT +33, NSW +2, QLD +107, SA 0, TAS +24, VIC +3, WA +5.

**Correct fix:** remove the 8 appended rows (`git checkout jaffle_shop/seeds/raw_customer_regions.csv`), or make the
regions CTE in `region_revenue` keep one row per customer (latest `updated_at`, e.g.
`qualify row_number() over (partition by customer_id order by updated_at desc) = 1`). A guard that would have caught it:
a `unique` test on `raw_customer_regions.customer_id`, or a singular test comparing `sum(region_revenue.revenue)` to
`sum(orders.amount)`.
