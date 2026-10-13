# Score: duplicate_payments

**Model:** Claude Sonnet via `claude -p --model sonnet` (Claude Code 2.1.296), file tools disabled, prompt = `prompt.md`.

**Root cause: found.** It named the exact file (`seeds/raw_payments.csv`), the exact rows (ids 114-125), the mechanism
(copies with fresh ids, so `unique` on `payment_id` passes), and reconciled the gap (12 rows = 180.00 = 1852 - 1672).
It also explained why `orders` still has one row per order and why `customer_lifetime_value` moves. Minor slip: its
"duplicates id" column is wrong for ids 122 and 124 (actual originals are 81 and 101) and it flagged that itself.

**Fix: made the output correct.** Its fix is "delete ids 114-125 and rebuild". Applied as
`git checkout jaffle_shop/seeds/raw_payments.csv && dbt build`: 28/28 pass, `stg_payments` back to 113 rows,
`sum(orders.amount)` back to 1672.0, customer 1 lifetime value back to 33.0. Its proposed singular test
(group by order_id, payment_method, amount having count > 1) returns exactly the 12 injected rows on the broken data
and 0 rows on the clean data, so it would have caught the case.

**Verdict:** pass on both root cause and fix. This case is easy for a model that gets the full seed file: the
duplicates are visible by eye and the gap arithmetic confirms them. A harder variant would hide the seed and give
only the warehouse row counts, or vary the amounts so the triple-match test no longer fires.
