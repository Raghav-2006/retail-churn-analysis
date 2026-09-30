"""One test per cleaning rule, on tiny hand-made tables where the right answer is obvious."""
import pandas as pd

from pipeline.transform import cancellation_rates, clean, is_non_product
from tests.conftest import make_raw


def step(log: pd.DataFrame, name: str) -> pd.Series:
    return log.set_index("step").loc[name]


def test_drops_null_customer_id(raw):
    sales, cancels, log, _ = clean(raw)
    assert sales["Customer ID"].notna().all() and cancels["Customer ID"].notna().all()
    assert step(log, "drop_null_customer_id")["removed"] == 1


def test_separates_cancellations(raw):
    sales, cancels, log, info = clean(raw)
    assert list(cancels["Invoice"]) == ["C103"]
    assert not sales["Invoice"].str.startswith("C").any()
    assert step(log, "separate_cancellations")["removed"] == 1
    # 1 cancelled invoice out of 9 distinct customer-attributed invoices (102 has no customer)
    assert info["cancellation"]["cancelled_invoices"] == 1
    assert info["cancellation"]["cancel_rate_by_invoices_pct"] == 11.11


def test_drops_nonpositive_quantity_and_price(raw):
    sales, _, log, _ = clean(raw)
    assert (sales["Quantity"] > 0).all() and (sales["Price"] > 0).all()
    assert not sales["Invoice"].isin(["104", "105"]).any()
    assert step(log, "drop_nonpositive_qty_price")["removed"] == 2


def test_drops_exact_duplicates(raw):
    sales, _, log, _ = clean(raw)
    assert (sales["Invoice"] == "100").sum() == 1
    assert step(log, "drop_exact_duplicates")["removed"] == 1


def test_drops_non_product_codes_and_lists_them(raw):
    sales, _, log, info = clean(raw)
    assert set(sales["StockCode"]) == {"A1", "B2"}
    assert info["non_product_codes_dropped"] == {"ADJUST2": 1, "C2": 1, "POST": 1, "TEST001": 1}
    assert step(log, "drop_non_product_codes")["removed"] == 4


def test_non_product_matching_is_exact_or_prefix():
    codes = pd.Series(["POST", "post", "POSTER1", "M", "M123", "ADJUST", "ADJUST2", "TEST001", "85123A", "BANK CHARGES"])
    assert list(is_non_product(codes)) == [True, True, False, True, False, True, True, True, False, True]


def test_adds_revenue(raw):
    sales, cancels, _, _ = clean(raw)
    assert (sales["Revenue"] == sales["Quantity"] * sales["Price"]).all()
    assert sales["Revenue"].sum() == 2 * 1.50 + 1 * 3.25
    assert cancels["Revenue"].sum() == -1.50


def test_final_table_and_log_are_consistent(raw):
    sales, _, log, _ = clean(raw)
    assert len(sales) == 2
    assert log["rows_after"].iloc[-1] == len(sales)
    # each step starts where the previous one ended
    assert (log["rows_before"].iloc[1:].to_numpy() == log["rows_after"].iloc[:-1].to_numpy()).all()
    assert sales["Customer ID"].dtype.kind == "i"


def test_cancellation_rate_by_revenue():
    df = make_raw([
        ("1", "A", "x", 10, "2011-01-01", 1.0, 1.0, "UK"),
        ("C2", "A", "x", -2, "2011-01-02", 1.0, 1.0, "UK"),
    ])
    rates = cancellation_rates(df)
    assert rates["cancel_rate_by_revenue_pct"] == 20.0
    assert rates["cancel_rate_by_invoices_pct"] == 50.0
