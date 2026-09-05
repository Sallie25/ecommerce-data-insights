# E-Commerce Data Insights

A data pipeline that takes two years of raw UK online retail transaction data (Dec 2009–Dec 2011,
1,067,371 line items) and turns it into cleaned, queryable business metrics — loaded into Postgres,
verified two independent ways (Pandas and SQL), and summarized for a non-technical audience in
`slides/`.

## Overview

The pipeline: raw Excel export → split into 4 relational tables (products, customers, sales,
returns) → cleaned → transformed → aggregated into business metrics → loaded into Postgres.
One aggregation is also benchmarked in Polars for a performance comparison.

Key findings are documented in `slides/ecommerce_insights_deck.pptx` and summarized in
`panel_talking_points.md`.

## Setup Instructions

**Requirements:** Python 3.13, [`uv`](https://docs.astral.sh/uv/), Docker.

```bash
# 1. Clone and enter the project
cd ecommerce_pipeline

# 2. Install dependencies
uv sync

# 3. Copy the environment template and fill in real values
cp .env.example .env

# 4. Start Postgres
docker compose up -d

# 5. Place the raw UCI Online Retail II Excel file at:
#    data/source/online_retail_data.xlsx
#    (download from https://archive.ics.uci.edu/dataset/502/online+retail+ii)

# 6. Run the pipeline, in order
uv run python src/split.py
uv run python src/clean.py
uv run python src/transform.py
uv run python src/aggregate.py

# 7. Apply the database schema
docker exec -i ecommerce-pg psql -U ecommerce_user -d ecommerce_db < ecommerce_db/schema.sql

# 8. Load cleaned data into Postgres
uv run python3 -m src.load_to_postgres

# 9. (Optional) Run the Pandas vs. Polars benchmark
uv run python src/benchmark.py
```

## Environment Variables

Defined in `.env` (see `.env.example`), loaded via `pydantic-settings` in `src/config.py`:

| Variable | Description | Example |
|---|---|---|
| `POSTGRES_USER` | Database username | `ecommerce_user` |
| `POSTGRES_PASSWORD` | Database password | (set your own) |
| `POSTGRES_HOST` | Database host | `localhost` |
| `POSTGRES_PORT` | Database port | `5432` |
| `POSTGRES_DB` | Database name | `ecommerce_db` |

`.env` is git-ignored — never commit real credentials.

## Test Instructions

```bash
uv run pytest -v
```

Test coverage in this submission focuses on the highest-risk logic: the cleaning and
aggregation steps that were the source of real bugs found during development (see
Known Limitations). Given project time constraints, test coverage is not comprehensive
across every module — see the note below for what to prioritize if extending this further.

## Architecture / Workflow

```
data/source/online_retail_data.xlsx  (raw UCI file)
        │
        ▼
   src/split.py         → data/raw/{products,customers,sales,returns}.csv + customers.json
        │
        ▼
   src/clean.py          → data/processed/sales_clean.parquet (+ customers, returns)
        │
        ▼
   src/transform.py      → data/processed/sales_transformed.parquet
        │                   (date features, line revenue, cancellation matching)
        ▼
   src/aggregate.py      → data/processed/summary_*.parquet
        │                   (revenue by month/country, top products, return rates)
        ▼
   src/load_to_postgres.py → Postgres tables: products, customers, sales, returns
        │
        ▼
   SQL queries (DBeaver) → slide deck insights
```

`src/benchmark.py` runs independently: re-implements one aggregation (monthly revenue) in
both Pandas and Polars and compares wall-clock time.

**Design decisions worth noting:**
- Product descriptions: when the same `stock_code` had multiple spellings, the most
  frequent spelling was kept as canonical.
- Cancellations (negative-quantity rows) are **retained** in `sales.csv` at the split
  stage and filtered out later in `clean.py` — this was a deliberate choice to keep the
  split step as a faithful raw-to-tables conversion, with business-rule filtering handled
  downstream.
- Returns are matched to their original sale using the same customer + product, taking
  the nearest prior sale by date (`pd.merge_asof`, backward direction). Returns with no
  matching prior sale (~11% of raw cancellations) are dropped, since the database schema
  requires every return to reference a real sale.

## Example Input/Output

**Input** (`data/raw/sales.csv`, one row per line item):
```
sale_id,invoice_number,stock_code,customer_id,quantity,unit_price,invoice_date
1,536365,85123A,17850,6,2.55,2010-12-01 08:26:00
```

**Output** (`data/processed/summary_monthly_revenue.parquet`, one row per month):
```
year_month  total_revenue  total_orders  total_items_sold
2010-11     1166457.73     2587          653070
```

## Known Limitations

- **~22.77% of sales have no `customer_id`** (likely guest checkouts) and are excluded
  from customer-level analysis rather than guessed at.
- **~11% of cancellations could not be matched** to a prior sale and were dropped rather
  than loaded with an incomplete/guessed foreign key.
- **Non-product stock codes** (`D` = discount, `POST` = postage, `M` = manual entry,
  `DOT` = misc.) were identified and excluded from product-ranking and return-rate
  calculations. This exclusion list was built by inspection during development; a more
  robust version would validate stock codes against the actual product catalog rather
  than a hardcoded list.
- **`repository.py` is minimal** (insert/bulk-insert only) rather than a full repository
  layer, and the main data load (`src/load_to_postgres.py`) uses `pandas.DataFrame.to_sql`
  directly rather than routing through repository classes, due to project time
  constraints. A future version would route all writes through the repository layer for
  consistency and centralized error handling (e.g. converting raw `psycopg` FK errors
  into a custom `ForeignKeyViolationError`).
- **Test coverage is not comprehensive** — see Test Instructions above.
- **DuckDB was not included** in the final performance comparison; only Pandas vs. Polars
  is benchmarked.
- One product (`stock_code 23843`) skews both the "top products by revenue" and "return
  rate" metrics due to a single very large bulk order that was also returned in full —
  documented in the slide deck rather than filtered out, since it's a real, notable event
  rather than a data error.

## Troubleshooting

- **`ModuleNotFoundError: No module named 'src'`** — run modules with `-m`, from the
  project root: `uv run python -m src.load_to_postgres`, not
  `uv run python src/load_to_postgres.py`.
- **`ModuleNotFoundError: No module named 'psycopg'`** — install with
  `uv add "psycopg[binary]"`. Note this is distinct from `psycopg2-binary`; the
  SQLAlchemy connection string here uses the `postgresql+psycopg://` dialect (psycopg3).
- **`ForeignKeyViolation` on loading `returns`** — this means `returns` still references
  `sale_id`s that were removed from `sales` during cleaning. Filter `returns` to only
  `sale_id`s present in the cleaned `sales` DataFrame before loading.
- **`DatatypeMismatch` on date columns** — Parquet round-trips can lose datetime typing
  depending on how a column was written; re-apply `pd.to_datetime(...)` before loading
  into Postgres.
- **Re-running `load_to_postgres.py` twice** without resetting the schema will fail on
  duplicate primary keys — re-apply `ecommerce_db/schema.sql` (it drops and recreates all
  tables) before reloading.
