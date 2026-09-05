"""
Tests for the cleaning and aggregation logic. Uses small, hand-built DataFrames
so these run instantly and don't depend on the real dataset or a running database.
"""
import pandas as pd
import pytest

from src.clean import drop_missing_customer_sales, dedupe_sales, filter_cancellations
from src.aggregate import revenue_by_month, top_n_products_by_revenue


@pytest.fixture
def sample_sales_df():
    """
    5 rows covering: a normal sale, a null customer_id, an exact duplicate,
    a cancellation (Invoice starts with 'C'), and a negative-quantity row.
    """
    return pd.DataFrame({
        "sale_id": [1, 2, 3, 4, 5],
        "invoice_number": ["536365", "536366", "536366", "C536367", "536368"],
        "stock_code": ["85123A", "22423", "22423", "85123A", "22423"],
        "customer_id": ["17850", None, None, "17850", "12345"],
        "quantity": [6, 3, 3, -2, -1],
        "unit_price": [2.55, 10.00, 10.00, 2.55, 10.00],
        "invoice_date": pd.to_datetime([
            "2010-12-01 08:26:00",
            "2010-12-01 09:00:00",
            "2010-12-01 09:00:00",
            "2010-12-01 10:00:00",
            "2010-12-01 11:00:00",
        ]),
    })


def test_drop_missing_customer_sales_removes_null_ids(sample_sales_df):
    result = drop_missing_customer_sales(sample_sales_df)
    assert result["customer_id"].isna().sum() == 0
    assert len(result) == 3  # rows 1, 4, 5 have a customer_id


def test_dedupe_sales_removes_exact_duplicates(sample_sales_df):
    cleaned = drop_missing_customer_sales(sample_sales_df)
    # rows 2 and 3 were exact duplicates but both had null customer_id, so they're
    # already gone; build a duplicate pair that survives the customer_id filter instead
    dup_df = pd.concat([sample_sales_df.iloc[[0]], sample_sales_df.iloc[[0]]], ignore_index=True)
    result = dedupe_sales(dup_df)
    assert len(result) == 1


def test_filter_cancellations_removes_negative_and_c_prefix(sample_sales_df):
    result = filter_cancellations(sample_sales_df)
    # row 4 (C-prefix) and row 5 (negative quantity) should both be gone
    assert not result["invoice_number"].str.startswith("C").any()
    assert (result["quantity"] > 0).all()
    assert len(result) == 3  # rows 1, 2, 3 remain


def test_revenue_by_month_sums_correctly():
    sales = pd.DataFrame({
        "year_month": ["2010-12", "2010-12", "2011-01"],
        "revenue": [15.30, 30.00, 25.50],
        "invoice_number": ["536365", "536366", "536370"],
        "quantity": [6, 3, 5],
    })
    result = revenue_by_month(sales)
    dec_row = result[result["year_month"] == "2010-12"].iloc[0]
    assert dec_row["total_revenue"] == pytest.approx(45.30)
    assert dec_row["total_orders"] == 2


def test_top_n_products_by_revenue_ranks_correctly():
    sales = pd.DataFrame({
        "stock_code": ["A", "A", "B", "C"],
        "revenue": [100, 50, 200, 10],
        "quantity": [1, 1, 1, 1],
        "invoice_number": ["1", "2", "3", "4"],
    })
    result = top_n_products_by_revenue(sales, n=2)
    assert len(result) == 2
    assert result.iloc[0]["stock_code"] == "B"  # highest total revenue (200)
    assert result.iloc[1]["stock_code"] == "A"  # second highest (100 + 50 = 150)
