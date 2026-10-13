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

Check every case in one go (clean build passes, injection changes a model's output, `dbt build` still passes after it):

```bash
uv run --project jaffle_shop check_case.py                      # all cases
uv run --project jaffle_shop check_case.py cases/gift_card_dollars
```

## Layout

- `jaffle_shop/` vendored from [dbt-labs/jaffle_shop_duckdb](https://github.com/dbt-labs/jaffle_shop_duckdb) (Apache-2.0, license kept),
  grown so every failure type has something to break: `raw_orders.ordered_at` timestamps (`order_date` is derived),
  a `raw_customer_regions` feed joined inside `region_revenue`, `customers.completed_lifetime_value` filtered on status,
  and `daily_revenue`, incremental with a three-day lookback. Clean totals are unchanged; the test count went 28 to 40
- `cases/<name>/` one folder per case: `inject.py`, `ground_truth.md`, `prompt.md`, `response.md`, `score.md`
- `check_case.py` runs each case's `inject.py` against a clean build and prints which tables changed
- `logs/` clean and broken `dbt build` logs
- `reports/` weekly reports

## Cases

| Case | What breaks | Tests | Sonnet: root cause, fix | Haiku: root cause, fix |
|---|---|---|---|---|
| duplicate_payments | 12 payments re-ingested with fresh ids | 28/28 pass (40/40 on the grown project) | yes, yes | yes, yes |
| gift_card_dollars | 12 gift card payments sent in dollars, divided by 100 again | 28/28 pass (40/40 on the grown project) | yes, yes | yes, yes |
| region_fanout | 8 customers' region rows re-sent, their payments counted twice in the regional report | 40/40 pass | yes, yes | yes, yes |
| status_renamed | orders feed renamed `status` to `order_status` from 1 March, old column arrives empty | 40/40 pass | yes, yes | yes, yes |
| utc_switch | orders feed switched from local time to UTC on 1 March, 7 orders land a day early | 40/40 pass | yes, partly | yes, partly |
| late_orders | 6 February orders backfilled in April, the incremental daily table never picks them up | 40/40 pass | yes, yes | yes, yes |
| status_title_case | app writes statuses in title case from 15 February, `accepted_values` widened to hide it | 40/40 pass | yes, yes | yes, yes |

Sonnet is `claude-sonnet-5-5` and Haiku is `claude-haiku-5-5`, both via `claude -p --setting-sources ""` on Claude Code
2.1.296 with tools disabled, isolated from this machine's hooks and global instructions. Each `score.md` has the run
details and the verdict of the earlier, confounded run. Prompts from `region_fanout` on hide the seed data.
