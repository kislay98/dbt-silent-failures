You are debugging a dbt project (dbt-core 1.12, dbt-duckdb adapter). `dbt build` completes with every test passing,
but ops says the daily revenue dashboard (`daily_revenue`) and `orders` disagree about February: 415.00 against
474.00 and 27 orders against 32, and the two have always matched. The extra orders are expected: one store's sync was
down through February and its backfill arrived in last night's load, six completed orders worth 65.00 that the store's
sync screen lists on 1, 3, 8, 13, 22 and 26 February, the first of them placed at 2018-02-01 04:48:19 on that screen; in the
warehouse `raw_orders` has that order (id 100) at 2018-01-31 23:18:19 and `orders` dates it 31 January. Ops reconciles
`orders` by `order_date` against the shop admin, which shows orders in the shop's local time, and until this load the two
agreed on every day since January.

Below are the project files (models and schema tests, without the seed data), the log of the nightly `dbt build`
(the raw tables are loaded by the ingestion job, so the log covers models and tests), the columns of the raw tables
as loaded in the warehouse, and a sample of the output tables.
Answer with:
1. The root cause, naming the exact file(s) and what is wrong in the data.
2. A fix (code or data change) that makes the output correct again.
3. A test that would have caught this.

## Included
Seeds: hidden
Log: models only
