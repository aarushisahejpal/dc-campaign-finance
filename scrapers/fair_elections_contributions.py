#!/usr/bin/env python3
"""
Download Fair Elections contributions, expenditures, and payment data
via the OCF Fair Elections public API.

Endpoints:
  POST /app/api/Public/ExportSearchContributions
  POST /app/api/Public/ExportSearchExpenditures
  POST /app/api/Public/ExportPaymentInformation
"""

import os

import pandas as pd
import requests

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
RAW_DIR = os.path.join(SCRIPT_DIR, "..", "data", "raw")
BASE_URL = "https://fairelections.ocf.dc.gov/app/api/Public"
ELECTION_YEAR = 2026

CONTRIB_COLUMNS = [
    "committee", "candidate", "report_name", "contribution_type", "schedule_code",
    "first_name", "middle_name", "last_name", "address1", "address2",
    "city", "state", "zip", "occupation", "employer_name",
    "employer_address1", "employer_address2", "employer_city", "employer_state",
    "employer_zip", "contribution_date", "amount", "mode_of_payment",
]

EXPEND_COLUMNS = [
    "committee", "candidate", "report_name", "expenditure_type", "schedule_code",
    "payee_first_name", "payee_middle_name", "payee_last_name",
    "payee_organization", "payee_address1", "payee_address2",
    "payee_city", "payee_state", "payee_zip",
    "payment_date", "amount", "purpose",
]

PAYMENT_COLUMNS = [
    "committee_name", "committee_code", "candidate_name", "election",
    "office", "registration_date", "registration_status", "certification_status",
    "base_payment", "base_cap", "matching_payment", "match_cap", "total_paid",
]


def download_and_parse(endpoint, params, columns, label):
    """POST to a Fair Elections API endpoint and parse the Excel response."""
    r = requests.post(
        f"{BASE_URL}/{endpoint}",
        json=params,
        headers={"Content-Type": "application/json"},
        timeout=120,
    )
    r.raise_for_status()

    xlsx_path = os.path.join(RAW_DIR, f"fe_{label}.xlsx")
    with open(xlsx_path, "wb") as f:
        f.write(r.content)

    df = pd.read_excel(xlsx_path, header=None, skiprows=3)
    df = df.iloc[:, :len(columns)]
    df.columns = columns
    df = df.dropna(subset=[columns[0]])
    # Remove header row artifacts
    df = df[df[columns[0]] != columns[0]]

    if "amount" in columns:
        df["amount"] = pd.to_numeric(df["amount"], errors="coerce").fillna(0)

    csv_path = os.path.join(RAW_DIR, f"fair_elections_{label}.csv")
    df.to_csv(csv_path, index=False)

    os.remove(xlsx_path)

    return df


def main():
    print("DC Fair Elections API downloader")
    print("=" * 50)
    os.makedirs(RAW_DIR, exist_ok=True)

    print(f"\nContributions (election year {ELECTION_YEAR}):")
    contrib = download_and_parse(
        "ExportSearchContributions",
        {"electionYear": ELECTION_YEAR},
        CONTRIB_COLUMNS,
        "contributions",
    )
    print(f"  {len(contrib):,} contributions, ${contrib['amount'].sum():,.2f}")

    print(f"\nExpenditures (election year {ELECTION_YEAR}):")
    expend = download_and_parse(
        "ExportSearchExpenditures",
        {"electionYear": ELECTION_YEAR},
        EXPEND_COLUMNS,
        "expenditures",
    )
    print(f"  {len(expend):,} expenditures, ${expend['amount'].sum():,.2f}")

    # Payment totals come from the Excel export on the Fair Elections
    # payment overview page. Use the existing fair_elections_export.xlsx
    # if available, or skip.
    fe_export = os.path.join(RAW_DIR, "fair_elections_export.xlsx")
    if os.path.exists(fe_export):
        import json
        payments = pd.read_excel(fe_export, header=None, skiprows=3)
        payments = payments.iloc[:, :len(PAYMENT_COLUMNS)]
        payments.columns = PAYMENT_COLUMNS
        payments = payments.dropna(subset=["committee_name"])
        payments = payments[payments["committee_name"] != "Committee Name"]
        for col in ["base_payment", "matching_payment", "total_paid"]:
            payments[col] = pd.to_numeric(payments[col], errors="coerce").fillna(0)
        records = payments.to_dict(orient="records")
        with open(os.path.join(RAW_DIR, "fair_elections.json"), "w") as f:
            json.dump(records, f, indent=2, default=str)
        print(f"\n  Payment totals: {len(records)} candidates, "
              f"${payments['total_paid'].sum():,.2f}")
    else:
        print("\n  No fair_elections_export.xlsx found — payment totals not updated")

    print("\nDone!")


if __name__ == "__main__":
    main()
