#!/usr/bin/env python3
"""
Process raw DC campaign finance CSVs into candidate-level summaries.
Reads from data/raw/, writes to data/processed/.
"""

import os
import sqlite3

import pandas as pd

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
RAW_DIR = os.path.join(SCRIPT_DIR, "..", "data", "raw")
PROCESSED_DIR = os.path.join(SCRIPT_DIR, "..", "data", "processed")
DB_PATH = os.path.join(SCRIPT_DIR, "..", "data", "dc_campaign_finance.db")


def clean_amount(s):
    """Convert '$1,234.56' to float."""
    if pd.isna(s):
        return 0.0
    return float(str(s).replace("$", "").replace(",", "").strip())


def load_contributions():
    path = os.path.join(RAW_DIR, "contributions.csv")
    if not os.path.exists(path):
        print("No contributions.csv found")
        return pd.DataFrame()
    df = pd.read_csv(path, dtype=str)
    df["amount_clean"] = df["Amount"].apply(clean_amount)
    df["receipt_date"] = pd.to_datetime(df["Receipt Date"], format="%m/%d/%Y", errors="coerce")
    return df


def load_expenditures():
    path = os.path.join(RAW_DIR, "expenditures.csv")
    if not os.path.exists(path):
        print("No expenditures.csv found")
        return pd.DataFrame()
    df = pd.read_csv(path, dtype=str)
    df["amount_clean"] = df["Amount"].apply(clean_amount)
    df["payment_date"] = pd.to_datetime(df["Payment Date"], format="%m/%d/%Y", errors="coerce")
    return df


def build_candidate_summary(contributions, expenditures):
    """Aggregate contributions and expenditures by committee."""
    rows = []

    if not contributions.empty:
        contrib_agg = contributions.groupby("Committee Name").agg(
            total_raised=("amount_clean", "sum"),
            num_contributions=("amount_clean", "count"),
            avg_contribution=("amount_clean", "mean"),
            max_contribution=("amount_clean", "max"),
            num_unique_donors=("Contributor Last Name", "nunique"),
            first_contribution=("receipt_date", "min"),
            last_contribution=("receipt_date", "max"),
            filer_type=("Filer Type", "first"),
        ).reset_index()
    else:
        contrib_agg = pd.DataFrame(columns=[
            "Committee Name", "total_raised", "num_contributions",
            "avg_contribution", "max_contribution", "num_unique_donors",
            "first_contribution", "last_contribution", "filer_type",
        ])

    if not expenditures.empty:
        expend_agg = expenditures.groupby("Committee Name").agg(
            total_spent=("amount_clean", "sum"),
            num_expenditures=("amount_clean", "count"),
        ).reset_index()
    else:
        expend_agg = pd.DataFrame(columns=[
            "Committee Name", "total_spent", "num_expenditures",
        ])

    summary = contrib_agg.merge(expend_agg, on="Committee Name", how="outer")
    # Only fill numeric columns with 0; leave dates/strings as-is
    for col in ["total_raised", "num_contributions", "avg_contribution",
                "max_contribution", "num_unique_donors", "total_spent", "num_expenditures"]:
        if col in summary.columns:
            summary[col] = summary[col].fillna(0)
    summary["cash_on_hand_proxy"] = summary["total_raised"] - summary["total_spent"]
    summary = summary.sort_values("total_raised", ascending=False)

    return summary


def build_donor_summary(contributions):
    """Top donors across all candidates."""
    if contributions.empty:
        return pd.DataFrame()

    # Build a full donor name
    contributions = contributions.copy()
    contributions["donor_name"] = (
        contributions["Contributor First Name"].fillna("")
        + " "
        + contributions["Contributor Last Name"].fillna("")
    ).str.strip()

    # For orgs, use org name
    mask = contributions["Contributor Organization Name"].fillna("").str.strip() != ""
    contributions.loc[mask, "donor_name"] = contributions.loc[mask, "Contributor Organization Name"]

    donor_agg = contributions.groupby("donor_name").agg(
        total_given=("amount_clean", "sum"),
        num_contributions=("amount_clean", "count"),
        num_committees=("Committee Name", "nunique"),
        committees=("Committee Name", lambda x: "; ".join(sorted(x.dropna().unique()))),
        city=("City", "first"),
        state=("State", "first"),
        employer=("Employer Name", "first"),
        occupation=("Occupation", "first"),
    ).reset_index()
    donor_agg = donor_agg.sort_values("total_given", ascending=False)

    return donor_agg


def build_spending_by_purpose(expenditures):
    """Spending breakdown by purpose."""
    if expenditures.empty:
        return pd.DataFrame()

    return expenditures.groupby(["Committee Name", "Purpose of Expenditure"]).agg(
        total=("amount_clean", "sum"),
        count=("amount_clean", "count"),
    ).reset_index().sort_values(["Committee Name", "total"], ascending=[True, False])


def save_to_sqlite(contributions, expenditures, candidate_summary, donor_summary, spending):
    """Write all tables to SQLite."""
    conn = sqlite3.connect(DB_PATH)

    if not contributions.empty:
        contributions.to_sql("contributions", conn, if_exists="replace", index=False)
    if not expenditures.empty:
        expenditures.to_sql("expenditures", conn, if_exists="replace", index=False)
    if not candidate_summary.empty:
        cs = candidate_summary.copy()
        for col in cs.columns:
            if pd.api.types.is_datetime64_any_dtype(cs[col]):
                cs[col] = cs[col].dt.strftime("%Y-%m-%d").fillna("")
        cs.to_sql("candidate_summary", conn, if_exists="replace", index=False)
    if not donor_summary.empty:
        donor_summary.to_sql("donor_summary", conn, if_exists="replace", index=False)
    if not spending.empty:
        spending.to_sql("spending_by_purpose", conn, if_exists="replace", index=False)

    conn.close()
    print(f"SQLite database: {DB_PATH}")


def main():
    os.makedirs(PROCESSED_DIR, exist_ok=True)

    print("Loading raw data...")
    contributions = load_contributions()
    expenditures = load_expenditures()

    print(f"  Contributions: {len(contributions):,} records")
    print(f"  Expenditures:  {len(expenditures):,} records")
    print()

    print("Building candidate summary...")
    candidate_summary = build_candidate_summary(contributions, expenditures)
    candidate_summary.to_csv(
        os.path.join(PROCESSED_DIR, "candidate_summary.csv"), index=False
    )
    print(f"  {len(candidate_summary)} committees")

    # Print top fundraisers
    if not candidate_summary.empty:
        print("\n  Top 10 fundraisers:")
        for _, row in candidate_summary.head(10).iterrows():
            print(f"    {row['Committee Name']}: ${row['total_raised']:,.0f} "
                  f"({int(row['num_contributions'])} contributions)")
        print()

    print("Building donor summary...")
    donor_summary = build_donor_summary(contributions)
    donor_summary.to_csv(
        os.path.join(PROCESSED_DIR, "donor_summary.csv"), index=False
    )
    print(f"  {len(donor_summary):,} unique donors")

    print("Building spending by purpose...")
    spending = build_spending_by_purpose(expenditures)
    spending.to_csv(
        os.path.join(PROCESSED_DIR, "spending_by_purpose.csv"), index=False
    )

    print("Saving to SQLite...")
    save_to_sqlite(contributions, expenditures, candidate_summary, donor_summary, spending)

    print("\nDone!")


if __name__ == "__main__":
    main()
