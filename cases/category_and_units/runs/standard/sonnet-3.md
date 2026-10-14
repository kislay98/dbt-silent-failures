claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 88 s | USD 0.171 | 2026-10-14 | project ad0b16b | prompt eb0b3a80ab67

The 136.00 gap is two separate problems stacked on each other. I can't see the seeds, so the exact rows below are inferred from the output tables and need confirming with the queries at the end.

## 1. Root cause

**Reconciling the numbers:**
- 1103 − 967 = 279 − 143 = **136.00**, so the whole gap is in March.
- Orders with status `delivered` sum to 134.02. That is 136.00 − 1.98, and 1.98 = 2 − 0.02.

**Problem A: `delivered` is not counted as completed (`models/customers.sql`, also `models/schema.yml` and `models/staging/schema.yml`).**
- `customers.sql` hard-codes `orders.status = 'completed'`.
- The 8 `delivered` orders appear to be March orders that the shop admin counts as completed. They are excluded from `completed_lifetime_value`.
- `delivered` was added to both `accepted_values` lists, so the tests pass. Nothing checks whether a status is classified as completed or not.

**Problem B: some `raw_payments` rows (`seeds/raw_payments`, loaded by the ingestion job) are in dollars, not cents.**
- `stg_payments.sql` divides every `amount` by 100, but a few coupon rows have tiny values such as 2, 23 and 7 instead of 200, 2300 and 700.
- This shows up as fractional coupon totals: March 18.27, April 0.33, and `orders.amount` values like 0.07 and 0.02.
- `daily_revenue` confirms it. It is incremental and only rebuilds the last 3 days, so it still holds the earlier, whole-dollar figures:
  - March is 622.00 against 595.27 in `orders` (diff 26.73).
  - April is 139.00 against 106.33 (diff 32.67).
  - Together that is 59.40, which is exactly 0.99 × 60, so about 60 dollars of coupon amounts were loaded 100× too small.
- Fixing the 2-cent coupon on a delivered order adds 1.98, so delivered becomes 136.00.
- With both fixes, March completed is 143 + 136 = 279 and since January it is 967 + 136 = 1103. Both match the shop admin.

Every test passes because none of them check units, status classification, or cross-model totals. `not_null` and `unique` can't see a value that is 100× too small.

## 2. Fix

**Data:**
- Reload `raw_payments` from the source in cents and fix the ingestion job where it mixes units.
- Don't patch it with a "small amount means dollars" heuristic in `stg_payments`.
- Then run `dbt build --full-refresh -s daily_revenue+` so the stale incremental history is rebuilt.

**Model:** define the completed statuses once and use them everywhere.

```sql
-- stg_orders.sql, in `renamed`
status,
status in ('completed', 'delivered') as is_completed
```

```sql
-- customers.sql
sum(case when orders.is_completed then amount else 0 end) as completed_amount
```

Confirm with finance that `delivered` counts as completed.

## 3. Tests

**Units, on the raw feed (this assumes the feed is whole dollars in cents, as the other rows suggest):**

```sql
-- tests/assert_raw_payments_in_cents.sql
select * from {{ ref('raw_payments') }} where amount % 100 != 0
```

**Reconciliation between models.** Two of these would have failed on this data (the stale `daily_revenue` and the `delivered` exclusion):

```sql
-- tests/assert_daily_revenue_matches_orders.sql
select 1
from (select sum(revenue) r from {{ ref('daily_revenue') }}) d,
     (select sum(amount) a from {{ ref('orders') }}) o
where abs(d.r - o.a) > 0.005
```

```sql
-- tests/assert_completed_ltv_matches_orders.sql
select 1
from (select sum(completed_lifetime_value) c from {{ ref('customers') }}) c,
     (select sum(amount) a from {{ ref('orders') }} where status in ('completed','delivered')) o
where abs(c.c - o.a) > 0.005
```

**Status classification:** keep `accepted_values` in sync with a single list of completed and not-completed statuses, so a new status can't be added without deciding which side it falls on.

## To confirm the inferred rows

```sql
select * from raw_payments where amount % 100 != 0;
select order_date, status, sum(amount) from orders where status = 'delivered' group by 1, 2;
```
