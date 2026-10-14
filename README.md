# dbt-silent-failures

Benchmark cases for "can a model find a data pipeline bug that raises no error". Each case takes dbt Labs'
`jaffle_shop` (DuckDB), breaks it with a script so `dbt build` still passes but the output is wrong, records
the ground truth, and scores a model's diagnosis.

## Reproduce

```bash
cd jaffle_shop
uv sync                 # needs uv and Python 3.13
uv run dbt build        # clean: 40 pass, sum(orders.amount) = 1672
cd ..
python3 cases/duplicate_payments/inject.py
cd jaffle_shop && uv run dbt build   # broken: 40 pass, sum(orders.amount) = 1852
git checkout seeds/raw_payments.csv  # undo
cd ..
python3 cases/gift_card_dollars/inject.py
cd jaffle_shop && uv run dbt build   # broken: 40 pass, sum(orders.amount) = 1469.05
git checkout seeds/raw_payments.csv  # undo
```

Check a number: `uv run python -c "import duckdb; print(duckdb.connect('jaffle_shop.duckdb').sql('select sum(amount) from orders'))"`

Check every case in one go (clean build passes, injection changes a model's output, `dbt build` still passes after it,
the key number in `ground_truth.md` matches), assemble a prompt, or run a model:

```bash
uv run --project jaffle_shop check_case.py                          # check all cases
uv run --project jaffle_shop check_case.py cases/gift_card_dollars  # check one
uv run --project jaffle_shop check_case.py cases/utc_switch --prompt      # print the full prompt
uv run --project jaffle_shop check_case.py cases/utc_switch --run haiku   # run a model, save runs/haiku-<n>.md
```

## Layout

- `jaffle_shop/` vendored from [dbt-labs/jaffle_shop_duckdb](https://github.com/dbt-labs/jaffle_shop_duckdb) (Apache-2.0, license kept),
  grown so every failure type has something to break: `raw_orders.ordered_at` timestamps (`order_date` is derived),
  a `raw_customer_regions` feed joined inside `region_revenue`, `customers.completed_lifetime_value` filtered on status,
  and `daily_revenue`, incremental with a three-day lookback. Clean totals are unchanged; the test count went 28 to 40
- `cases/<name>/` one folder per case: `inject.py`, `ground_truth.md` (with the key number the check verifies), `prompt.md`
  (symptom text, whether seeds are shown, whether the build log is the full build or a models-and-tests build, and the
  queries whose results the model gets), `score.md`, and `runs/<model>-<n>.md` for every model run
- `check_case.py` checks a case, assembles its full prompt at run time (`--prompt`) and runs a model on it (`--run sonnet`)
- `logs/` clean and broken `dbt build` logs
- `reports/` weekly reports

## Cases

| Case | Type | What breaks | Sonnet, found / fixed | Haiku, found / fixed |
|---|---|---|---|---|
| duplicate_payments | duplicate rows | 12 payments re-ingested with fresh ids | 3 / 3 | 3 / 3 |
| duplicate_orders | duplicate rows | a two-week slice of February orders replayed with fresh ids, their payments re-keyed to them | 3 / 3 | 3 / 3 |
| gift_card_dollars | unit change | 12 gift card payments sent in dollars, divided by 100 again | 3 / 3 | 3 / 3 |
| coupon_cents | unit change | 6 of 13 coupon payments switch to dollars from 15 March, the rest stay in cents | 3 / 3 | 2 / 2 |
| transfers_in_micros | unit change | bank transfers from 15 March reported in micro-units, ten payments 10,000 times too large | 3 / 3 | 2 / 3 |
| region_fanout | join fan-out | 8 customers' region rows re-sent, payments counted twice in the regional report | 3 / 3 | 3 / 3 |
| regions_fanout_history | join fan-out | the regions feed starts carrying history, 6 customers move region and are counted in both | 3 / 3 | 3 / 3 |
| status_renamed | schema drift | orders feed renamed `status` to `order_status` from 1 March, old column empty | 3 / 3 | 3 / 3 |
| payment_method_renamed | schema drift | payments feed writes `method` from 1 March, `payment_method` empty, the per-method columns go to 0 | 3 / 3 | 3 / 3 |
| join_drops_rows | null flood | second leg of every split payment arrives with no order id, 13 payments vanish from the marts | 3 / 3 | 2 / 2 |
| null_order_dates | null flood | one week of March orders lands without a timestamp, March by order date is short, the dashboard keeps the old March | 3 / 3 | 3 / 3 |
| null_payment_amounts | null flood | second instalments of 8 split orders land with no amount and `sum` skips them | 3 / 3 | 3 / 2 |
| utc_switch | timezone shift | orders feed switched from local time to UTC on 1 March, 7 orders land a day early | 3 / 0 | 3 / 0 |
| utc_switch_blind | timezone shift | the same, prompt without the example order | 1 / 0 | 2 / 0 |
| utc_reexport | timezone shift | the whole orders history re-exported in UTC, 14 orders land a day early across every month | 3 / 3 | 3 / 3 |
| late_orders | late data | 6 February orders backfilled in April, the incremental daily table never picks them up | 3 / 3 | 3 / 3 |
| late_payments | late data | second instalments for 8 February orders land in April, the daily table keeps the old revenue | 3 / 3 | 3 / 3 |
| status_title_case | category change | app writes statuses in title case from 15 February, `accepted_values` widened to hide it | 3 / 3 | 3 / 3 |
| status_new_value | category change | app writes `delivered` for completed orders from 10 March, `accepted_values` widened to hide it | 3 / 3 | 3 / 3 |
| late_and_duplicate | mixed | the late orders plus 12 replayed payments in one load | 3 / 3 | 3 / 3 |
| units_and_rename | mixed | the payment-method rename plus the coupon dollars in one load | 3 / 1 | 0 / 0 |

Each run is scored on two yes/no questions: **root cause found**, the run names the change that broke the data (naming
only the mechanism, or hedging between the right cause and a wrong one, is no), and **complete fix**, the run's fix
applied to the broken data makes every affected value match the clean data (a fix that corrects some values is no).
The cells are runs out of three. `results.csv` has every run and `score.md` in each case has the per-run notes and what
was verified against the data. Every case passes `check_case.py` with 40/40 tests on both sides. Sonnet is
`claude-sonnet-5-5` and Haiku is `claude-haiku-5-5`, run with `claude -p --setting-sources ""` on Claude Code 2.1.296,
tools off, three times each. Prompts from `region_fanout` on hide the seed data; cases from `duplicate_orders` on
(week 4 and later) get a models-and-tests build log instead of the full build (`Log: models only` in `prompt.md`), and
every run from week 5 on records the sha256 of the prompt it answered.

![Root cause found and complete fix by failure type and model](reports/success_by_type.svg)
