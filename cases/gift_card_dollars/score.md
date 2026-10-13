# Score: gift_card_dollars

**Model:** Claude Sonnet via `claude -p --model sonnet --tools ""` (Claude Code 2.1.296), all tools disabled, prompt = `prompt.md`.

**Root cause: found.** It named the exact line (`amount / 100` in `models/staging/stg_payments.sql`), the exact rows
(all 12 `gift_card` rows in `seeds/raw_payments.csv`, listed by payment id, order id, raw and staged value, all correct),
and the mechanism (rows already in dollars get divided by 100 again). It reconciled every number in the prompt:
non-gift-card methods 411 + 185 + 871 = 1467.00, plus 205.00 = 1672.00 settlement, plus 2.05 = 1469.05 observed. It also
got order 87 right (26.30 shown, 56.00 expected) without being told, and explained why all 28 tests pass (none check values).
Bonus: it also spotted that `models/schema.yml` documents a `total_order_amount` column on `customers` that the model
never produces (the real column is `customer_lifetime_value`). That is a genuine docs bug in upstream jaffle_shop,
unrelated to the injection, and it correctly flagged it as separate.

**Fix: made the output correct.** Its fix is a `case when payment_method = 'gift_card' then amount else amount / 100 end`
in `stg_payments`. Applied on the broken seed and rebuilt: `sum(orders.amount)` back to 1672.0, `sum(gift_card_amount)`
back to 205.0, order 9 back to 23.0, order 87 back to 56.0, customer 53 back to 39.0, all 12 orders and 10 customers
match the clean values. Caveat it raised itself: this bakes the provider's mistake into the model, and if the feed goes
back to cents the same rows become 100x too large. The ground truth prefers fixing the seed; its fix is correct for the
data as given. Its reconciliation test (`provider_statement` seed + singular test comparing `sum(gift_card_amount)` and
`sum(amount)` to the statement) returns 2 rows on the broken data without the fix (`dbt build` reports ERROR=1) and
0 rows on clean data and on fixed data (30/30 pass with the extra seed and test).

**Verdict:** pass on both root cause and fix. Like dev's case this is easy with the full seed in the prompt: 12 rows with
two-digit amounts sit among 101 rows with four-digit amounts, and the 100x ratio in the symptom gives it away. A harder
variant would convert only part of the gift card rows (so the method total no longer reconciles cleanly), or pick a
method whose rows are not visibly different in magnitude, or hide the seed and give only aggregates.
