# Score: gift_card_dollars

**Runs:** `claude -p --setting-sources "" --model sonnet --tools "" --output-format json` and the same with `--model haiku` (Claude Code 2.1.296),
isolated from this machine's hooks and global instructions, all tools disabled, one turn each, prompt = `prompt.md`
(seeds included, 28-test project). Sonnet is `claude-sonnet-5-5` (22 s, USD 0.08), response in `runs/sonnet-1.md`. Haiku is `claude-haiku-5-5`
(32 s, USD 0.006), response in `runs/haiku-1.md`.

## Claude Sonnet

**Root cause: found.** It named the `amount / 100` line in `stg_payments.sql`, all 12 gift card rows with id, order,
raw, staged and correct value (all right), 205 against 2.05, the reconciliation 1469.05 - 2.05 + 205.00 = 1672.00,
customer 53 at 16.23 instead of 39.00, and why no structural test can see a wrong value. It flagged the
`total_order_amount` doc line.

**Fix: made the output correct.** `case when payment_method = 'gift_card' then amount else amount / 100 end`. Applied and
rebuilt: every order matches the clean data, 1672.0 and 205.0. Its singular test with hard-coded control totals (gift
card 205.00, all methods 1672.00) fails with 2 rows on the broken data, passes after the fix and on clean data (41/41);
it noted the totals are tied to this snapshot and belong in a seed.

**Verdict:** pass on both root cause and fix.

## Claude Haiku

**Root cause: found.** The same line and the same 12 rows (all right), the arithmetic, order 87 to 56.00 and order 58 to
24.00 (both right), and why the internal totals agree with each other while wrong.

**Fix: made the output correct.** It fixed the data instead of the model: the 12 gift card rows back to cents. The 12
values it listed reproduce the clean seed byte for byte; rebuilt, every order matches the clean data, 1672.0 and 205.0.
Its test is a `control_payment_totals` seed (gift card 205.00) with a per-method reconciliation on `stg_payments`: fails
with 1 row on the broken data and passes after the fix.

**Verdict:** pass on both root cause and fix.

**Earlier runs (default config, confounded by the ponytail hook, 17 to 22 Oct):** Sonnet pass and pass; Haiku pass and
pass. No verdict changed; the models now split on where to fix (model or seed), and both are correct for this data.

## Repeat runs

Two more runs per model, same command, prompt assembled by `check_case.py` from the week 3 working tree.

| Run | Root cause | Fix | Notes |
|---|---|---|---|
| sonnet-2 | yes | yes | Same 12 rows and fix; proposes per-method control totals (411, 185, 871, 205) as a seed. |
| sonnet-3 | yes | yes | Same; unit test plus control totals. |
| haiku-2 | yes | yes | Same; order 58 at 24.00 right. |
| haiku-3 | yes | yes | Same; unit test plus a per-order provider statement. |

The per-method control-total test is the same shape as the one verified for haiku-1 and fails on the broken data.

## Opus runs (week 6)

Three runs of `claude-opus-5-5`, same command, prompt assembled by `check_case.py` from the project at f769fdf, before
the week 6 lookup extension, so the same project the other two models saw; verdicts in `results.csv`.

| Run | Root cause | Fix | Notes |
|---|---|---|---|
| opus-1 | yes | yes | The twelve rows listed, cents restored in the seed, full refresh; a sub-dollar guard and a control-total seed. |
| opus-2 | yes | yes | Same. |
| opus-3 | yes | yes | Same. |

Opus: 3 of 3 found, 3 of 3 fixed.

## Standard sample

**Runs:** three per model, week 8, on the standard output sample (the same queries on every case: row counts, the raw
column list, monthly totals of the output tables and ten rows of every table picked by hash); `runs/standard/prompt.md`
is the frozen prompt every run answered and `runs/standard/` holds the responses. `claude -p --setting-sources ""
--model <model> --tools "" --output-format json` (Claude Code 2.1.299), project ad0b16b. Same rubric; Defects is how
many of the case's injected defects the run named as a cause.

| Run | Root cause | Fix | Defects | Notes |
|---|---|---|---|---|
| sonnet-1 | yes | yes | 1 | Named the 12 dollar-unit `gift_card` rows in `raw_payments.csv` against the `amount / 100` line in `stg_payments.sql`, all 12 ids and the 1467.00 + 205.00 = 1672.00 reconciliation; fix is the verified `case when payment_method = 'gift_card' then amount else amount / 100 end` plus `dbt build --full-refresh -s stg_payments+`. Read `daily_revenue` at 1655.17 as Jan to Mar rows left over from a clean build. |
| sonnet-2 | yes | yes | 1 | Same 12 rows with raw and staged values and the same `case when` in `stg_payments.sql` with `dbt build --full-refresh`; reconciled `daily_revenue` April 122.17 against a correct 139.00 and proposed a control-total seed per method. |
| sonnet-3 | yes | yes | 1 | Same rows, same `case when` fix and full refresh; a unit test on `stg_payments` plus a `provider_statements` seed with a reconciliation test. |
| haiku-1 | yes | yes | 1 | Named the 12 rows and the `/ 100` line; fix restores the 12 seed rows to cents (the values match the clean seed) with `dbt build --full-refresh`, and suggests making `daily_revenue` a table. |
| haiku-2 | yes | yes | 1 | Same 12 rows tabled with the correct values; fix is the `case when` shape with `cast(amount as double)` on the gift card branch, full refresh of `stg_payments+`; unit test plus a per-method settlement seed. |
| haiku-3 | yes | yes | 1 | Same 12 rows with the cents each should hold; recommended fix restores the seed to cents with a full refresh of `daily_revenue`, the `case when` offered as the alternative; provider-statement seed and reconciliation test. |
| opus-1 | yes | yes | 1 | Same 12 rows restored to cents in the seed with `dbt build --full-refresh`; the `case when` named as the alternative only if the feed is contractually in dollars; a sub-dollar guard on `raw_payments.amount` and a control-total seed. |
| opus-2 | yes | yes | 1 | Same; seed to cents preferred, `case when` as the alternative, full refresh; a sub-dollar guard on `stg_payments` and a per-date `daily_revenue` against `orders` reconciliation. |
| opus-3 | yes | yes | 1 | Same 12 rows; offers both verified shapes as Option A (seed to cents) and Option B (`case when`) and defers the choice, "Before choosing, confirm with whoever owns the feed which one is true", but either option with the full refresh it prescribes restores the clean data, so the hedge changes nothing here. |

**Verification.** Every fix matches a shape in the Verification paragraph above.

**What it says.** All nine standard runs match the nine curated runs: 9 of 9 found and 9 of 9 fixed, with the same two fix shapes (Sonnet and haiku-2 patch `stg_payments`, the rest restore the seed) and the same reconciliation 1469.05 - 2.05 + 205.00 = 1672.00. The symptom text handed over the three finance numbers and the sample's per-month `gift_card` column (0.52, 0.29, 0.81, 0.43) and the single `gift_card 0.28` row in the `stg_payments` hash sample let every run tie them to the 12 seed rows, which it listed from `raw_payments.csv`. The one thing the standard sample added was the `daily_revenue` monthly totals (496, 415, 622, 122.17, summing to 1655.17), which every run read correctly as Jan to Mar rows left from a clean build plus a rebuilt window carrying order 96 at 0.17, and every run added `--full-refresh` for it. No run went wrong; the only hedge is opus-3 leaving the choice between two correct fixes to the feed owner.
