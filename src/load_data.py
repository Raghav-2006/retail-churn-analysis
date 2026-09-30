"""Phase 0: load both sheets of the UCI Online Retail II workbook into one parquet file.

Run from the repo root:  python src/load_data.py
Reading the 45 MB Excel file takes a few minutes, so we do it once and every
later step reads data/raw.parquet instead.
"""
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
XLSX = DATA / "online_retail_II.xlsx"
RAW = DATA / "raw.parquet"


def load_raw_excel(path: Path = XLSX) -> pd.DataFrame:
    # sheet_name=None returns every sheet; the workbook has one per year
    sheets = pd.read_excel(path, sheet_name=None, dtype={"Invoice": str, "StockCode": str})
    for name, df in sheets.items():
        print(f"sheet {name!r}: {len(df):,} rows")
    return pd.concat(sheets.values(), ignore_index=True)


def main() -> None:
    df = load_raw_excel()
    # Description can hold stray non-string values; force a clean string column for parquet
    df["Description"] = df["Description"].astype("string")
    df["Country"] = df["Country"].astype("string")
    df.to_parquet(RAW, index=False)

    print(f"\nsaved {RAW.relative_to(ROOT)}")
    print(f"shape: {df.shape}")
    print("\ndtypes:\n", df.dtypes, sep="")
    print("\nnull counts:\n", df.isna().sum(), sep="")
    print(f"\ndate range: {df['InvoiceDate'].min()} -> {df['InvoiceDate'].max()}")


if __name__ == "__main__":
    main()
