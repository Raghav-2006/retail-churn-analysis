"""Extract: load both sheets of the UCI Online Retail II workbook into one table.

Reading the 45 MB Excel file takes a few minutes, so it is parsed once into
data/raw.parquet and every later run reads the parquet instead.

    python -m pipeline.extract          # (re)build data/raw.parquet
"""
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
XLSX = DATA / "online_retail_II.xlsx"
RAW = DATA / "raw.parquet"
URL = "https://archive.ics.uci.edu/static/public/502/online+retail+ii.zip"


def load_raw_excel(path: Path = XLSX) -> pd.DataFrame:
    # sheet_name=None returns every sheet; the workbook has one per year
    sheets = pd.read_excel(path, sheet_name=None, dtype={"Invoice": str, "StockCode": str})
    for name, df in sheets.items():
        print(f"sheet {name!r}: {len(df):,} rows")
    df = pd.concat(sheets.values(), ignore_index=True)
    # Description can hold stray non-string values; force a clean string column for parquet
    df["Description"] = df["Description"].astype("string")
    df["Country"] = df["Country"].astype("string")
    return df


def extract(refresh: bool = False) -> pd.DataFrame:
    """Return the raw table, parsing the Excel file only if the parquet cache is missing."""
    if refresh or not RAW.exists():
        if not XLSX.exists():
            raise FileNotFoundError(
                f"{XLSX.relative_to(ROOT)} not found. Download {URL} and unzip it into data/."
            )
        df = load_raw_excel()
        DATA.mkdir(exist_ok=True)
        df.to_parquet(RAW, index=False)
    return pd.read_parquet(RAW)


def describe(df: pd.DataFrame) -> None:
    print(f"shape: {df.shape}")
    print("\ndtypes:\n", df.dtypes, sep="")
    print("\nnull counts:\n", df.isna().sum(), sep="")
    print(f"\ndate range: {df['InvoiceDate'].min()} -> {df['InvoiceDate'].max()}")


if __name__ == "__main__":
    describe(extract(refresh=True))
