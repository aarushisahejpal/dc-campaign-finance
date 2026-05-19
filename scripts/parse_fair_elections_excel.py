#!/usr/bin/env python3
"""
Parse the Fair Elections Excel export into fair_elections.json.

Usage:
  1. Download from fairelections.ocf.dc.gov -> Fair Elections Candidates
     Payment and Information -> Download Results (Excel)
  2. Save as data/raw/fair_elections_export.xlsx
  3. Run: python scripts/parse_fair_elections_excel.py
"""

import json
import os
import sys

import pandas as pd

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
EXCEL_PATH = os.path.join(SCRIPT_DIR, "..", "data", "raw", "fair_elections_export.xlsx")
JSON_PATH = os.path.join(SCRIPT_DIR, "..", "data", "raw", "fair_elections.json")
CSV_PATH = os.path.join(SCRIPT_DIR, "..", "data", "raw", "fair_elections.csv")


def main():
    if not os.path.exists(EXCEL_PATH):
        print(f"No Excel file found at {EXCEL_PATH}")
        print("Download from: fairelections.ocf.dc.gov -> Payment and Information -> Download Results (Excel)")
        sys.exit(1)

    df = pd.read_excel(EXCEL_PATH, header=None, skiprows=3)
    df.columns = [
        "committee_name", "committee_code", "candidate_name", "election",
        "office", "registration_date", "registration_status", "certification_status",
        "base_payment", "base_cap", "matching_payment", "match_cap", "total_paid",
    ]
    df = df.dropna(subset=["committee_name"])
    df = df[df["committee_name"] != "Committee Name"]

    for col in ["base_payment", "matching_payment", "total_paid", "base_cap", "match_cap"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    df["registration_date"] = pd.to_datetime(
        df["registration_date"], errors="coerce"
    ).dt.strftime("%Y-%m-%d")

    records = df.to_dict(orient="records")

    with open(JSON_PATH, "w") as f:
        json.dump(records, f, indent=2, default=str)

    df.to_csv(CSV_PATH, index=False)

    print(f"{len(records)} candidates, ${df['total_paid'].sum():,.2f} total")
    print(f"Saved to {JSON_PATH} and {CSV_PATH}")


if __name__ == "__main__":
    main()
