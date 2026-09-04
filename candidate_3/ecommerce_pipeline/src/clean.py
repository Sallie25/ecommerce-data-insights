# src/clean.py
"""
This code cleans up raw shopping files after they are loaded.
It fills in missing info, throws away exact copies, gets rid of canceled orders,
and makes sure all numbers and words are formatted correctly.
It reads files directly from the data/raw folder.
"""

from pathlib import Path

import pandas as pd
from loguru import logger

# Folder locations for our project files
SOURCE_PATH = Path(__file__).resolve().parent.parent
DATA_DIR = SOURCE_PATH / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"


def load_sales(raw_dir: Path = RAW_DIR) -> pd.DataFrame:
    """
    Opens the sales.csv file and fixes the data types for each column.

    This makes sure dates act like real dates, ID numbers stay as numbers,
    and missing customer IDs are turned into clean empty spaces.
    """
    sales_file = raw_dir / "sales.csv"
    if not sales_file.exists():
        raise FileNotFoundError(f"Sales file not found at {sales_file}")

    sales = pd.read_csv(
        sales_file,
        dtype={
            "sale_id": "int64",
            "invoice_number": "str",
            "stock_code": "str",
            "quantity": "int64",
            "unit_price": "float64",
            "customer_id": "str",
        },
    )

    sales["invoice_date"] = pd.to_datetime(sales["invoice_date"])
    
    # Change different types of empty text into a standard empty value (None)
    sales["customer_id"] = (
        sales["customer_id"]
        .str.strip()
        .replace({"nan": None, "<NA>": None, "": None})
    )

    logger.info(f"Loaded sales.csv | Initial Rows: {len(sales):,}")
    return sales


def drop_missing_customer_sales(sales: pd.DataFrame) -> pd.DataFrame:
    """
    Removes any receipt row that does not have a customer ID attached to it.

    We throw away guest purchases so every sale links back to a known shopper,
    keeping our customer lists accurate.
    """
    missing_mask = sales["customer_id"].isna()
    missing_count = missing_mask.sum()

    if missing_count > 0:
        logger.warning(
            f"Dropping {missing_count:,} rows with missing customer_id "
            f"({(missing_count / len(sales)) * 100:.2f}% of sales rows)."
        )

    cleaned_sales = sales[~missing_mask].copy()
    return cleaned_sales.reset_index(drop=True)


def dedupe_sales(sales: pd.DataFrame) -> pd.DataFrame:
    """
    Looks for exact copycat rows in the table and keeps only the first one.

    Checks the receipt number, item code, quantity, date, and customer ID
    to spot duplicates.
    """
    dedupe_cols = ["invoice_number", "stock_code", "quantity", "invoice_date", "customer_id"]
    initial_rows = len(sales)
    
    cleaned_sales = sales.drop_duplicates(subset=dedupe_cols, keep="first").copy()
    duplicates_removed = initial_rows - len(cleaned_sales)

    if duplicates_removed > 0:
        logger.info(f"Removed {duplicates_removed:,} exact duplicate sales records.")

    return cleaned_sales.reset_index(drop=True)


def filter_cancellations(sales: pd.DataFrame) -> pd.DataFrame:
    """
    Removes returned items and canceled orders.

    Throws out any row where the quantity is zero or less, or where the 
    receipt number starts with the letter 'C' for canceled.
    """
    initial_rows = len(sales)
    is_cancellation = sales["invoice_number"].str.startswith("C", na=False) | (sales["quantity"] <= 0)

    cancellations_count = is_cancellation.sum()
    if cancellations_count > 0:
        logger.info(
            f"Filtered out {cancellations_count:,} cancellation/negative quantity rows."
        )

    cleaned_sales = sales[~is_cancellation].copy()
    return cleaned_sales.reset_index(drop=True)


def clean_sales(raw_dir: Path = RAW_DIR) -> pd.DataFrame:
    """
    Runs the full cleanup routine in order: load data -> drop missing customers -> remove duplicates -> remove cancellations.
    """
    logger.info("Starting Sales Data Cleaning Pass...")

    sales = load_sales(raw_dir)
    sales = drop_missing_customer_sales(sales)
    sales = dedupe_sales(sales)
    cleaned_sales = filter_cancellations(sales)

    logger.success(
        f"Sales cleaning complete | Final Clean Rows: {len(cleaned_sales):,}"
    )
    return cleaned_sales


if __name__ == "__main__":
    # Test the cleanup step directly when running this file
    df = clean_sales(RAW_DIR)