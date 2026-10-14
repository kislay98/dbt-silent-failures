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
