# dbt-silent-failures

Benchmark cases for "can a model find a data pipeline bug that raises no error". Each case takes dbt Labs'
`jaffle_shop` (DuckDB), breaks it with a script so `dbt build` still passes but the output is wrong, records
the ground truth, and scores a model's diagnosis.

## Reproduce

```bash
cd jaffle_shop
uv sync                 # needs uv and Python 3.13
uv run dbt build        # clean: 28 pass, sum(orders.amount) = 1672
cd ..
python3 cases/duplicate_payments/inject.py
cd jaffle_shop && uv run dbt build   # broken: 28 pass, sum(orders.amount) = 1852
git checkout seeds/raw_payments.csv  # undo
cd ..
python3 cases/gift_card_dollars/inject.py
cd jaffle_shop && uv run dbt build   # broken: 28 pass, sum(orders.amount) = 1469.05
git checkout seeds/raw_payments.csv  # undo
```

Check a number: `uv run python -c "import duckdb; print(duckdb.connect('jaffle_shop.duckdb').sql('select sum(amount) from orders'))"`

## Layout

- `jaffle_shop/` vendored from [dbt-labs/jaffle_shop_duckdb](https://github.com/dbt-labs/jaffle_shop_duckdb) (Apache-2.0, license kept)
- `cases/<name>/` one folder per case: `inject.py`, `ground_truth.md`, `prompt.md`, `response.md`, `score.md`
- `logs/` clean and broken `dbt build` logs
- `reports/` weekly reports

## Cases

| Case | What breaks | Tests | Model | Found root cause | Fix worked |
|---|---|---|---|---|---|
| duplicate_payments | 12 payments re-ingested with fresh ids | 28/28 pass | Claude Sonnet | yes | yes |
| gift_card_dollars | 12 gift card payments sent in dollars, divided by 100 again | 28/28 pass | Claude Sonnet | yes | yes |
