"""Transform: the cleaning rules for Online Retail II.

Steps run in a fixed order and each one appends a row to a cleaning log, so the
cost of every decision (how many rows it removed, and why) is visible. Every
rule is a pure function of the input DataFrame, so tests/test_transform.py can
check each one on a tiny hand-made table.
"""
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"

# Codes that are fees, postage or bookkeeping entries rather than products.
# Customers don't "buy" postage, so leaving these in would inflate revenue and
# product counts. ADJUST* and TEST* are matched as prefixes (ADJUST2, TEST001, ...).
NON_PRODUCT_CODES = {"POST", "DOT", "M", "D", "C2", "BANK CHARGES", "AMAZONFEE", "PADS", "CRUK", "S", "B"}
NON_PRODUCT_PREFIXES = ("ADJUST", "TEST", "GIFT_")


class CleaningLog:
    def __init__(self) -> None:
        self.rows = []

    def record(self, step: str, before: int, after: int, reason: str) -> None:
        removed = before - after
        self.rows.append({
            "step": step,
            "rows_before": before,
            "rows_after": after,
            "removed": removed,
            "pct_removed": round(100 * removed / before, 2) if before else 0.0,
            "reason": reason,
        })

    def to_frame(self) -> pd.DataFrame:
        return pd.DataFrame(self.rows)


def is_non_product(codes: pd.Series) -> pd.Series:
    upper = codes.str.upper()
    return upper.isin(NON_PRODUCT_CODES) | upper.str.startswith(NON_PRODUCT_PREFIXES)


def cancellation_rates(df: pd.DataFrame) -> dict:
    """Cancellation rate by invoice count and by revenue, on customer-attributed rows."""
    is_cancel = df["Invoice"].str.startswith("C")
    line_rev = df["Quantity"] * df["Price"]
    n_invoices = df["Invoice"].nunique()
    n_cancel = df.loc[is_cancel, "Invoice"].nunique()
    # Cancellation lines carry negative quantity; compare their absolute value to gross sales
    cancel_rev = -line_rev[is_cancel].sum()
    gross_rev = line_rev[~is_cancel & (line_rev > 0)].sum()
    return {
        "cancelled_invoices": int(n_cancel),
        "total_invoices": int(n_invoices),
        "cancel_rate_by_invoices_pct": round(100 * n_cancel / n_invoices, 2),
        "cancelled_revenue": round(float(cancel_rev), 2),
        "gross_revenue": round(float(gross_rev), 2),
        "cancel_rate_by_revenue_pct": round(100 * cancel_rev / gross_rev, 2),
    }


def clean(raw: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict]:
    """Return (clean_df, cancellations_df, log_df, info)."""
    log = CleaningLog()
    info = {}
    df = raw.copy()

    # 1. Revenue we can't tie to a customer is useless for customer-level analysis
    n = len(df)
    df = df[df["Customer ID"].notna()]
    log.record("drop_null_customer_id", n, len(df), "revenue can't be attributed to a customer")

    # 2. Cancellations are kept aside: not sales, but useful later as a return-rate feature
    info["cancellation"] = cancellation_rates(df)
    is_cancel = df["Invoice"].str.startswith("C")
    cancellations = df[is_cancel].copy()
    n = len(df)
    df = df[~is_cancel]
    log.record("separate_cancellations", n, len(df), "invoice starts with 'C'; saved to cancellations.parquet")

    # 3. Non-positive quantity/price rows are adjustments or free samples, not sales
    n = len(df)
    df = df[(df["Quantity"] > 0) & (df["Price"] > 0)]
    log.record("drop_nonpositive_qty_price", n, len(df), "Quantity <= 0 or Price <= 0 is not a sale")

    # 4. Exact duplicates are almost certainly double-logged lines
    n = len(df)
    df = df.drop_duplicates()
    log.record("drop_exact_duplicates", n, len(df), "identical rows are double-logged lines")

    # 5. Postage, fees, manual adjustments and test items are not products
    mask = is_non_product(df["StockCode"])
    info["non_product_codes_dropped"] = (
        df.loc[mask].groupby("StockCode").size().sort_values(ascending=False).to_dict()
    )
    n = len(df)
    df = df[~mask]
    log.record("drop_non_product_codes", n, len(df), "postage, fees, adjustments and test codes are not products")

    # 6. Revenue per line; Customer ID is float only because of the NaNs we removed
    df = df.assign(Revenue=df["Quantity"] * df["Price"], **{"Customer ID": df["Customer ID"].astype(int)})
    cancellations = cancellations.assign(
        Revenue=cancellations["Quantity"] * cancellations["Price"],
        **{"Customer ID": cancellations["Customer ID"].astype(int)},
    )

    return df.reset_index(drop=True), cancellations.reset_index(drop=True), log.to_frame(), info


def run(save: bool = True) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, dict]:
    from pipeline.extract import extract

    raw = extract()
    clean_df, cancel_df, log_df, info = clean(raw)
    if save:
        clean_df.to_parquet(DATA / "clean.parquet", index=False)
        cancel_df.to_parquet(DATA / "cancellations.parquet", index=False)
    return raw, clean_df, cancel_df, log_df, info


if __name__ == "__main__":
    _, clean_df, _, log_df, info = run()
    print(log_df.to_string(index=False))
    print(info)
