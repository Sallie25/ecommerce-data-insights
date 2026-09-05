# src/aggregate.py
"""
This code groups our transformed shopping data to answer big business questions.
It calculates total money made by month, country, and product.
It also works out which items get returned the most often.
It reads sales_transformed.parquet from data/processed and saves finished summaries back to data/processed.
"""

from pathlib import Path

import pandas as pd
from loguru import logger

# Folder locations for our project files
SOURCE_PATH = Path(__file__).resolve().parent.parent
DATA_DIR = SOURCE_PATH / "data"
PROCESSED_DIR = DATA_DIR / "processed"


def load_transformed_sales(processed_dir: Path = PROCESSED_DIR) -> pd.DataFrame:
    """
    Opens the transformed sales Parquet file created by src/transform.py.
    This file already includes date features, line revenue, and cancellation flags.
    """
    file_path = processed_dir / "sales_transformed.parquet"
    if not file_path.exists():
        raise FileNotFoundError(
            f"Transformed sales file not found at {file_path}. Run src/transform.py first!"
        )

    sales = pd.read_parquet(file_path)
    logger.info(f"Loaded transformed sales data | Total Rows: {len(sales):,}")
    return sales


def revenue_by_month(sales: pd.DataFrame) -> pd.DataFrame:
    """
    Group all sales by month using the pre-calculated 'year_month' feature,
    adding up total revenue for each month.

    Answers: 'which month made the most money?'
    """
    # Uses 'year_month' created in src/transform.py
    monthly_summary = (
        sales.groupby("year_month", as_index=False)
        .agg(
            total_revenue=("revenue", "sum"),
            total_orders=("invoice_number", "nunique"),
            total_items_sold=("quantity", "sum"),
        )
        .sort_values("year_month")
        .reset_index(drop=True)
    )

    logger.info(f"Calculated monthly revenue summary across {len(monthly_summary)} months.")
    return monthly_summary


def revenue_by_country(sales: pd.DataFrame, processed_dir: Path = PROCESSED_DIR) -> pd.DataFrame:
    """
    First connect sales to customers_clean.parquet to get country.
    Then group by country, add up revenue.

    Answers: 'which country buys the most from us?'
    """
    customers_file = processed_dir / "customers_clean.parquet"

    # Fall back to raw customer CSV if clean parquet is not available yet
    if not customers_file.exists():
        customers_file = DATA_DIR / "raw" / "customers.csv"

    if not customers_file.exists():
        raise FileNotFoundError(f"Customer reference file not found at {customers_file}")

    if customers_file.suffix == ".parquet":
        customers = pd.read_parquet(customers_file)
    else:
        customers = pd.read_csv(customers_file, dtype={"customer_id": "str"})

    # Ensure matching customer_id strings
    customers["customer_id"] = customers["customer_id"].astype(str).str.strip()
    sales_copy = sales.copy()
    sales_copy["customer_id"] = sales_copy["customer_id"].astype(str).str.strip()

    # Join sales to customers to attach country
    merged_sales = sales_copy.merge(
        customers[["customer_id", "country"]],
        on="customer_id",
        how="left",
    )

    country_summary = (
        merged_sales.groupby("country", as_index=False)
        .agg(
            total_revenue=("revenue", "sum"),
            total_orders=("invoice_number", "nunique"),
            unique_customers=("customer_id", "nunique"),
        )
        .sort_values("total_revenue", ascending=False)
        .reset_index(drop=True)
    )

    logger.info(f"Calculated revenue by country for {len(country_summary)} countries.")
    return country_summary


def top_n_products_by_revenue(sales: pd.DataFrame, n: int = 10) -> pd.DataFrame:
    """
    Group by product stock code, add up revenue, sort highest to lowest, keep top n.

    Answers: 'what are our best-selling products?'
    """
    product_summary = (
        sales.groupby("stock_code", as_index=False)
        .agg(
            total_revenue=("revenue", "sum"),
            total_quantity_sold=("quantity", "sum"),
            total_orders=("invoice_number", "nunique"),
        )
        .sort_values("total_revenue", ascending=False)
        .head(n)
        .reset_index(drop=True)
    )

    logger.info(f"Extracted top {n} products by total revenue.")
    return product_summary


def return_rate_by_product(sales, processed_dir=PROCESSED_DIR):
    returns_file = processed_dir / "returns_clean.parquet"
    if not returns_file.exists():
        returns_file = DATA_DIR / "raw" / "returns.csv"

    sales = sales[~sales["stock_code"].isin(["D", "POST", "M", "DOT"])]

    sold_counts = sales.groupby("stock_code", as_index=False).agg(units_sold=("quantity", "sum"))

    if returns_file.exists():
        returns = pd.read_parquet(returns_file) if returns_file.suffix == ".parquet" else pd.read_csv(returns_file)

        # Step 1: connect returns to sales using sale_id, to borrow stock_code
        returns_with_product = returns.merge(
            sales[["sale_id", "stock_code"]], on="sale_id", how="left"
        )

        # Step 2: NOW count returns per product
        returned_counts = returns_with_product.groupby("stock_code", as_index=False).agg(
            units_returned=("quantity", "sum")
        )
    else:
        returned_counts = pd.DataFrame(columns=["stock_code", "units_returned"])

    product_returns = sold_counts.merge(returned_counts, on="stock_code", how="left")
    product_returns["units_returned"] = product_returns["units_returned"].fillna(0)
    product_returns["return_rate"] = product_returns["units_returned"] / product_returns["units_sold"]

    return product_returns.sort_values("return_rate", ascending=False).reset_index(drop=True)


def run_aggregations(processed_dir: Path = PROCESSED_DIR) -> None:
    """
    Master function to run all aggregation steps and save final summaries to disk.
    """
    logger.info("Starting Data Aggregation Pass...")

    # Load transformed dataset directly (revenue and date features already present)
    sales = load_transformed_sales(processed_dir)

    # Calculate summaries
    monthly_df = revenue_by_month(sales)
    country_df = revenue_by_country(sales, processed_dir)
    top_products_df = top_n_products_by_revenue(sales, n=10)
    return_rate_df = return_rate_by_product(sales, processed_dir)

    # Save summary tables to data/processed/
    monthly_df.to_parquet(processed_dir / "summary_monthly_revenue.parquet", index=False)
    country_df.to_parquet(processed_dir / "summary_country_revenue.parquet", index=False)
    top_products_df.to_parquet(processed_dir / "summary_top_products.parquet", index=False)
    return_rate_df.to_parquet(processed_dir / "summary_product_return_rates.parquet", index=False)

    logger.success("All aggregation summaries calculated and saved to data/processed/!")


if __name__ == "__main__":
    run_aggregations(PROCESSED_DIR)