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
  (symptom text, whether seeds are shown, and the queries whose results the model gets), `score.md`, and `runs/<model>-<n>.md`
  for every model run
- `check_case.py` checks a case, assembles its full prompt at run time (`--prompt`) and runs a model on it (`--run sonnet`)
- `logs/` clean and broken `dbt build` logs
- `reports/` weekly reports

## Cases

| Case | Type | What breaks | Sonnet, runs right | Haiku, runs right |
|---|---|---|---|---|
| duplicate_payments | duplicate rows | 12 payments re-ingested with fresh ids | 3 of 3 | 3 of 3 |
| gift_card_dollars | unit change | 12 gift card payments sent in dollars, divided by 100 again | 3 of 3 | 3 of 3 |
| region_fanout | join fan-out | 8 customers' region rows re-sent, payments counted twice in the regional report | 3 of 3 | 3 of 3 |
| region_fanout_quiet | join fan-out | the same with 2 rows | 3 of 3 | 3 of 3 |
| status_renamed | schema drift | orders feed renamed `status` to `order_status` from 1 March, old column empty | 3 of 3 | 3 of 3 |
| join_drops_rows | null flood | second leg of every split payment arrives with no order id, 13 payments vanish from the marts | 3 of 3 | 2 of 3 |
| utc_switch | timezone shift | orders feed switched from local time to UTC on 1 March, 7 orders land a day early | 0 of 3 (root cause 3, fix partial 3) | 0 of 3 (root cause 3, fix partial 3) |
| utc_switch_blind | timezone shift | the same, prompt without the example order | 0 of 3 (root cause 1) | 0 of 3 (root cause 2) |
| late_orders | late data | 6 February orders backfilled in April, the incremental daily table never picks them up | 3 of 3 | 3 of 3 |
| late_and_duplicate | mixed | the late orders plus 12 replayed payments in one load | 3 of 3 | 3 of 3 |
| status_title_case | category change | app writes statuses in title case from 15 February, `accepted_values` widened to hide it | 3 of 3 | 3 of 3 |

A run is right when it names the root cause and its fix makes the output correct; `results.csv` has every run and
`score.md` in each case has the per-run notes. Every case passes `check_case.py` with 40/40 tests on both sides.
Sonnet is `claude-sonnet-5-5` and Haiku is `claude-haiku-5-5`, run with `claude -p --setting-sources ""` on Claude Code
2.1.296, tools off, three times each. Prompts from `region_fanout` on hide the seed data.

![Success by failure type and model](reports/success_by_type.svg)
