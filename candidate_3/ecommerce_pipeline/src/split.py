from pathlib import Path

import pandas as pd
from loguru import logger

SOURCE_PATH = Path(__file__).resolve().parent.parent
DATA_DIR = SOURCE_PATH /'data'
SOURCE_DATA_DIR = DATA_DIR / 'source'
RAW_DIR = DATA_DIR / 'raw'
PROCESSED_DIR = DATA_DIR / 'processed'
EXCEL_FILE = SOURCE_DATA_DIR/ 'online_retail_data.xlsx'
PARQUET_PATH = SOURCE_DATA_DIR / 'Online_retail_data.parquet'


# print(SOURCE_PATH)
# print(SOURCE_DATA_DIR)
# print(EXCEL_FILE)


# Reads all the sheets into a dictionary of dataframes
def load_raw_sheets(excel_path: Path = EXCEL_FILE, parquet_path: Path = PARQUET_PATH) -> pd.DataFrame:

    # check if the path is a parquet file
    if PARQUET_PATH.exists():
        # Loading parquet file
        return pd.read_parquet(parquet_path)

    # Loading all sheets from Excel file
    all_sheets = pd.read_excel(excel_path, sheet_name=None, engine='calamine') # sheet_name=None tells pandas to return all the sheets in the file

    # Concatenating all dataframes vertically
    combined_df = pd.concat(all_sheets.values(), ignore_index=True)

    # Rest the index so that rows are uniquesly numbered from 0
    combined_df = combined_df.reset_index(drop=True)

    # Dealing with mixed columns to avoid error when converting to parquet
    object_cols = combined_df.select_dtypes(include=['object']).columns
    for col in object_cols:
        combined_df[col] = combined_df[col].astype(str)

    # converting my column names to lowercase
    combined_df.columns = combined_df.columns.str.lower()

    # rename columns
    combined_df = combined_df[['invoice','stockcode','description','quantity','invoicedate','price','customer id','country']].rename(
        columns={
            "invoice": "invoice_number",
            "stockcode": "stock_code",
            "price": "unit_price",
            "invoicedate": "invoice_date",
            "customer id": "customer_id",}
        )

    # Making sure the directory we want to save the parquet file actually exists before pandas write to it
    PARQUET_PATH.parent.mkdir(parents=True, exist_ok=True)


    # Save to parquet to load faster using pyArrow backend
    combined_df.to_parquet(parquet_path, engine='pyarrow')

    # Return the combined Dataframes
    return combined_df


def derive_products(raw: pd.DataFrame) -> pd.DataFrame:
    """Build the products dimension: unique StockCode + one canonical Description.

        Strategy Decision:
        - We resolve multiple Description variations for a single StockCode by selecting
        the MOST FREQUENT (mode) non-null Description associated with that StockCode.
        - If there is a tie, we pick the first most frequent one.
        """
    # Starting with a fresh copy containing the two product columns
    valid_products = raw[['stock_code', 'description']].copy()

    # Ensuring that strings are stripped of all leading/trailing spaces
    valid_products[['stock_code','description']] = valid_products[['stock_code', 'description']].apply(lambda col: col.str.strip().replace("", None))

     # Filter out missing/empty stock_codes and Descriptions
    valid_products = valid_products.dropna()

    # Count occurences of each (stock_code, description) pair
    description_counts = (
        valid_products.groupby(['stock_code','description'], observed=True).size().reset_index(name='count')
    )

    # Sort by count descending and keep the most frequent Description per stock_code
    description_counts = description_counts.sort_values(by=['stock_code', 'count'], ascending = [True, False])

    
    products_df = description_counts.drop_duplicates(subset=['stock_code'], keep='first')

    # Drop the count column
    products_df = products_df.drop(columns=['count'])

    # Reset_index
    products_df = products_df.reset_index(drop=True)

    return products_df




def derive_customers(raw: pd.DataFrame) -> pd.DataFrame:
    """Build the customers dimension: unique customer_id + canonical country.

    Strategy Decision:
    - Drops null Customer IDs
    - Resolves multi-country Customer IDs by picking the MOST FREQUENT country.
    """


    # Filter out null customer_id
    valid_customers = raw.dropna(subset=['customer_id'])[['customer_id', 'country']].copy()

    # Format customer_id to clean integer string and strip country strings
    valid_customers['customer_id'] = (valid_customers['customer_id'].astype('Int64').astype(str).str.strip())

    valid_customers['country'] = valid_customers['country'].astype(str).str.strip()

    # Find the most frequent country per customer id
    country_counts = valid_customers.groupby(['customer_id','country'], observed=True).size().reset_index(name='count')

    country_counts = country_counts.sort_values(by=['customer_id','count'], ascending=[True, False])

    # Drop duplicates
    customers_df = country_counts.drop_duplicates(subset=['customer_id'], keep='first')

    # clean up columns and resset index
    customers_df = (
        customers_df.drop(columns=['count']).reset_index(drop=True)
    )

    return customers_df




def derive_sales(raw: pd.DataFrame) -> pd.DataFrame:
    """Build the sales fact table: one row per raw invoice line item.

    Strategy Decision:
    - Retains all raw transaction lines, including cancellations (Invoice starting with 'C')
      null customer_id, and other business filtering is left for clean.py
    - Generates a synthetic integer primary key (`sale_id`).
    """
    raw_df = raw.copy()

    # Filter for sales
    sales_df = raw_df[['invoice_number','stock_code','unit_price','invoice_date','customer_id','quantity']].copy()

    # Strip string identifiers
    sales_df["invoice_number"] = sales_df["invoice_number"].astype(str).str.strip()
    sales_df["stock_code"] = sales_df["stock_code"].astype(str).str.strip()

    # Handle missing numeric values safely in derive_sales and carrying out numeric casts
    sales_df["quantity"] = sales_df["quantity"].fillna(0).astype(int)
    sales_df["unit_price"] = sales_df["unit_price"].fillna(0.0).astype(float)

    # Datetime conversion
    sales_df["invoice_date"] = pd.to_datetime(sales_df["invoice_date"])

    # Safely format customer_id 
    sales_df["customer_id"] = (
        sales_df["customer_id"]
        .astype("Int64") # Nullable integer handles NaNs without breaking
        .astype(str)
        .replace("<NA>", None)
    )

    # Generate sequential PK starting at 1
    sales_df.insert(0, "sale_id", range(1, len(sales_df) + 1))

    return sales_df


def derive_returns(sales: pd.DataFrame) -> pd.DataFrame:
    """Build the returns fact table using pd.merge_asof for temporal matching.

    Strategy Decision (Option 3A):
    - Performs a backward lookup to match returns to their prior sales.
    - Drops unmatched returns (where no prior sale exists in our dataset) to satisfy
      the NOT NULL constraint on returns.sale_id in the downstream database schema.
    - Logs a warning detailing the exact number of unmatched returns dropped.
    """
    is_cancellation = sales["invoice_number"].str.startswith("C", na=False) | (sales["quantity"] < 0)

    normal_sales = sales[~is_cancellation].dropna(subset=["customer_id"]).sort_values("invoice_date")
    cancellations = sales[is_cancellation].dropna(subset=["customer_id"]).sort_values("invoice_date")

    matched = pd.merge_asof(
        cancellations,
        normal_sales[["sale_id", "stock_code", "customer_id", "invoice_date"]],
        on="invoice_date",
        by=["customer_id", "stock_code"],
        direction="backward",
        suffixes=("_return", "_sale"),
    )

    returns_df = pd.DataFrame(
        {
            "return_id": range(1, len(matched) + 1),
            "sale_id": matched["sale_id_sale"],
            "quantity": matched["quantity"].abs(),
            "return_date": matched["invoice_date"],
        }
    )

    # Count unmatched prior sales before dropping
    unmatched_count = returns_df["sale_id"].isna().sum()
    if unmatched_count > 0:
        logger.warning(
            f"Dropping {unmatched_count:,} unmatched return records to enforce "
            f"NOT NULL constraint on returns.sale_id."
        )

    # Drop rows without a matched sale_id and cast FK to integer
    returns_df = returns_df.dropna(subset=["sale_id"]).copy()
    returns_df["sale_id"] = returns_df["sale_id"].astype(int)

    # Re-index primary key sequentially
    returns_df["return_id"] = range(1, len(returns_df) + 1)

    return returns_df.reset_index(drop=True)



def write_outputs(
    products: pd.DataFrame,
    customers: pd.DataFrame,
    sales: pd.DataFrame,
    returns: pd.DataFrame,
    raw_dir: Path = RAW_DIR,
) -> None:
    """Write products, customers, sales, returns to CSV and customers to JSON.

    Steps:
    1. Ensure destination raw_dir exists.
    2. Export all DataFrames to CSV without the index.
    3. Export customers DataFrame to JSON (records orient).
    """
    # 1. Create directory if it doesn't exist
    raw_dir.mkdir(parents=True, exist_ok=True)

    # 2. Write CSV outputs
    products.to_csv(raw_dir / "products.csv", index=False)
    customers.to_csv(raw_dir / "customers.csv", index=False)
    sales.to_csv(raw_dir / "sales.csv", index=False)
    returns.to_csv(raw_dir / "returns.csv", index=False)

    # 3. Write JSON output (orient='records' creates a list of customer objects)
    customers.to_json(raw_dir / "customers.json", orient="records", indent=4)




def main() -> None:
    """Orchestrate the ETL pipeline: load -> derive 4 tables -> write outputs."""
    logger.info("Starting E-Commerce Data Engineering Pipeline...")

    # Load Raw Data
    logger.info("Loading raw dataset...")
    raw_df = load_raw_sheets()
    logger.success(f"Raw dataset loaded successfully | Total Rows: {len(raw_df):,}")

    # Derive Products Dimension
    logger.info("Deriving Products dimension...")
    products_df = derive_products(raw_df)
    logger.success(f"Products dimension created | Unique Products: {len(products_df):,}")

    # Derive Customers Dimension
    logger.info("Deriving Customers dimension...")
    customers_df = derive_customers(raw_df)
    logger.success(f"Customers dimension created | Unique Customers: {len(customers_df):,}")

    # Derive Sales Fact Table
    logger.info("Deriving Sales fact table...")
    sales_df = derive_sales(raw_df)
    logger.success(f"Sales fact table created | Total Line Items: {len(sales_df):,}")

    # Derive Returns Fact Table
    logger.info("Deriving Returns fact table via pd.merge_asof...")
    returns_df = derive_returns(sales_df)
    matched_count = returns_df["sale_id"].notna().sum()
    logger.success(
        f"Returns fact table created | Total Returns: {len(returns_df):,} "
        f"(Matched to prior sales: {matched_count:,})"
    )

    # Export Data
    logger.info(f"Writing outputs to directory: {RAW_DIR}")
    write_outputs(products_df, customers_df, sales_df, returns_df, PROCESSED_DIR)
    logger.success("All outputs successfully written to disk! Pipeline complete.")


if __name__ == "__main__":
    main()







































































# if __name__ == '__main__':
    # df= load_raw_sheets(EXCEL_FILE)
    # df= load_raw_sheets(PARQUET_PATH)
    # print(df.shape)
    # print(df.head(10))
    # print(df.columns.to_list())
    # print("\nSample df Info:")
    # print(df.info())

    # Generate the product dimension table
    # products = derive_products(df)
    
    # print(f"Raw dataset rows: {len(df):,}")
    # print(f"Unique products catalog rows: {len(products):,}")
    # print("\nSample Products Catalog:")
    # print(products.head(100))

    # Generate the customer dimension table
    # customers = derive_customers(df)
    
    # print(f"Raw dataset rows: {len(df):,}")
    # print(f"Unique customers catalog rows: {len(customers):,}")
    # print("\nSample customers Catalog:")
    # print(customers.head(100))

    # Generate and print sales fact table
    # sales = derive_sales(df)
    # print(f"\nSales fact table rows: {len(sales):,}")
    # print(sales.head(5))

    # # Test derive_returns
    # returns = derive_returns(sales)
    # print(f"Derived returns table rows: {len(returns):,}")

    # print("\n--- Sample Returns Table ---")
    # print(returns.head(10))

    # print("\n--- Matching Stats ---")
    # matched_count = returns["sale_id"].notna().sum()
    # unmatched_count = returns["sale_id"].isna().sum()
    # print(f"Matched to prior sale: {matched_count:,} ({matched_count / len(returns):.1%})")
    # print(f"Unmatched (no prior sale found): {unmatched_count:,} ({unmatched_count / len(returns):.1%})")