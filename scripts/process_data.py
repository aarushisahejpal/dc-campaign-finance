#!/usr/bin/env python3
"""
Process raw DC campaign finance CSVs into candidate-level summaries.
Joins financial data with the filer registry to map committees to races.
Reads from data/raw/, writes to data/processed/.
"""

import json
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


def load_registry():
    """Load filer registry JSON into a DataFrame for joining."""
    path = os.path.join(RAW_DIR, "filer_registry.json")
    if not os.path.exists(path):
        print("  WARNING: No filer_registry.json found")
        return pd.DataFrame()
    with open(path) as f:
        data = json.load(f)
    if not data:
        return pd.DataFrame()
    df = pd.DataFrame(data)
    return df


def build_registry_lookup(registry):
    """Build a CommitteeName -> {office, party, candidate, committee_id} lookup.

    Some candidates have multiple committees (e.g. Yaida Ford has two).
    We keep all of them — the CommitteeName in the financial data will match.
    """
    if registry.empty:
        return {}

    lookup = {}
    for _, row in registry.iterrows():
        name = row.get("CommitteeName", "")
        if not name:
            continue
        lookup[name] = {
            "committee_id": row.get("CommitteeId"),
            "candidate_name": str(row.get("CandidateName", "")).strip(),
            "first_name": str(row.get("FirstName", "")).strip(),
            "last_name": str(row.get("LastName", "")).strip(),
            "office": row.get("Office", ""),
            "party": row.get("PartyName", ""),
            "election_year": row.get("ElectionYear"),
            "filer_type": row.get("FilerType", ""),
        }
    return lookup


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


def enrich_with_registry(df, lookup):
    """Add office, party, candidate_name, committee_id columns from registry."""
    df["office"] = df["Committee Name"].map(lambda x: lookup.get(x, {}).get("office", ""))
    df["party"] = df["Committee Name"].map(lambda x: lookup.get(x, {}).get("party", ""))
    df["candidate_name"] = df["Committee Name"].map(lambda x: lookup.get(x, {}).get("candidate_name", ""))
    df["committee_id"] = df["Committee Name"].map(lambda x: lookup.get(x, {}).get("committee_id", ""))
    return df


def build_candidate_summary(contributions, expenditures, lookup):
    """Aggregate contributions and expenditures by committee, enriched with race info."""
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
    for col in ["total_raised", "num_contributions", "avg_contribution",
                "max_contribution", "num_unique_donors", "total_spent", "num_expenditures"]:
        if col in summary.columns:
            summary[col] = summary[col].fillna(0)
    summary["cash_on_hand_proxy"] = summary["total_raised"] - summary["total_spent"]

    # Enrich with registry data
    summary["office"] = summary["Committee Name"].map(lambda x: lookup.get(x, {}).get("office", ""))
    summary["party"] = summary["Committee Name"].map(lambda x: lookup.get(x, {}).get("party", ""))
    summary["candidate_name"] = summary["Committee Name"].map(lambda x: lookup.get(x, {}).get("candidate_name", ""))
    summary["committee_id"] = summary["Committee Name"].map(lambda x: lookup.get(x, {}).get("committee_id", ""))
    summary["election_year"] = summary["Committee Name"].map(lambda x: lookup.get(x, {}).get("election_year", ""))

    summary = summary.sort_values("total_raised", ascending=False)
    return summary


def build_race_summary(candidate_summary):
    """Aggregate by race/office."""
    if candidate_summary.empty:
        return pd.DataFrame()

    has_office = candidate_summary[candidate_summary["office"] != ""]
    if has_office.empty:
        return pd.DataFrame()

    race_agg = has_office.groupby("office").agg(
        num_committees=("Committee Name", "count"),
        total_raised=("total_raised", "sum"),
        total_spent=("total_spent", "sum"),
        top_fundraiser=("Committee Name", "first"),  # already sorted by total_raised desc
    ).reset_index().sort_values("total_raised", ascending=False)

    return race_agg


def build_donor_summary(contributions):
    """Top donors across all candidates."""
    if contributions.empty:
        return pd.DataFrame()

    contributions = contributions.copy()
    contributions["donor_name"] = (
        contributions["Contributor First Name"].fillna("")
        + " "
        + contributions["Contributor Last Name"].fillna("")
    ).str.strip()

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


def build_site_json(contributions, expenditures, lookup, metadata):
    """Build the JSON file that powers the landing page.

    - Includes all 2026-registered committees, even those with $0 activity
    - Combines multiple committees under the same candidate
    """
    site_path = os.path.join(SCRIPT_DIR, "..", "data", "site_data.json")

    # Start from 2026-tagged committees in the registry (so $0 filers still appear)
    all_names = set(
        name for name, info in lookup.items()
        if info.get("election_year") == 2026 or info.get("election_year") == "2026"
    )
    if not contributions.empty:
        all_names.update(contributions["Committee Name"].unique())
    if not expenditures.empty:
        all_names.update(expenditures["Committee Name"].unique())

    # Build per-committee raw data first
    raw_committees = []
    for name in sorted(all_names):
        info = lookup.get(name, {})
        c = contributions[contributions["Committee Name"] == name] if not contributions.empty else pd.DataFrame()
        e = expenditures[expenditures["Committee Name"] == name] if not expenditures.empty else pd.DataFrame()

        raw_committees.append({
            "committee_name": name,
            "candidate_name": info.get("candidate_name", ""),
            "office": info.get("office", ""),
            "party": info.get("party", ""),
            "committee_id": info.get("committee_id", ""),
            "filer_type": info.get("filer_type", ""),
            "_contrib_df": c,
            "_expend_df": e,
        })

    # Group by candidate+office to combine multiple committees per candidate
    from collections import defaultdict
    candidate_groups = defaultdict(list)
    for rc in raw_committees:
        cname = rc["candidate_name"]
        office = rc["office"]
        # Non-candidate committees (IECs, initiatives, PACs) keep their own identity
        if not cname or rc["filer_type"] not in ("Principal Campaign Committee", "Exploratory Committee"):
            key = rc["committee_name"]
        else:
            key = f"{cname}||{office}"
        candidate_groups[key].append(rc)

    committees = []
    for key, group in candidate_groups.items():
        # Merge all contributions and expenditures across the candidate's committees
        c_frames = [g["_contrib_df"] for g in group if not g["_contrib_df"].empty]
        e_frames = [g["_expend_df"] for g in group if not g["_expend_df"].empty]
        c = pd.concat(c_frames) if c_frames else pd.DataFrame()
        e = pd.concat(e_frames) if e_frames else pd.DataFrame()

        # Top 10 donors (includes individuals, corporations, labor, PACs)
        top_donors = []
        if not c.empty:
            dc = c.copy()
            dc["donor"] = (c["Contributor First Name"].fillna("") + " " + c["Contributor Last Name"].fillna("")).str.strip()
            mask = c["Contributor Organization Name"].fillna("").str.strip() != ""
            dc.loc[mask, "donor"] = c.loc[mask, "Contributor Organization Name"]
            top10 = dc.groupby("donor").agg(
                total=("amount_clean", "sum"),
                contributor_type=("Contributor Type", "first"),
                state=("State", "first"),
            ).sort_values("total", ascending=False).head(10)
            for name, row in top10.iterrows():
                if not name:
                    continue
                top_donors.append({
                    "name": name,
                    "total": round(row["total"], 2),
                    "type": row.get("contributor_type", ""),
                    "state": row.get("state", ""),
                })

        # Spending by purpose
        spending = []
        if not e.empty and "Purpose of Expenditure" in e.columns:
            purposes = e.groupby("Purpose of Expenditure")["amount_clean"].sum().sort_values(ascending=False)
            spending = [{"purpose": p, "total": round(v, 2)} for p, v in purposes.items()]

        primary = group[0]
        committee_names = [g["committee_name"] for g in group]

        # DC vs out-of-DC breakdown
        dc_total = 0
        out_of_dc_total = 0
        if not c.empty and "State" in c.columns:
            dc_total = round(c[c["State"] == "DC"]["amount_clean"].sum(), 2)
            out_of_dc_total = round(c[c["State"] != "DC"]["amount_clean"].sum(), 2)

        committees.append({
            "committee_name": ", ".join(committee_names),
            "candidate_name": primary["candidate_name"],
            "office": primary["office"],
            "party": primary["party"],
            "committee_id": primary["committee_id"],
            "filer_type": primary["filer_type"],
            "total_raised": round(c["amount_clean"].sum(), 2) if not c.empty else 0,
            "total_spent": round(e["amount_clean"].sum(), 2) if not e.empty else 0,
            "num_contributions": len(c),
            "num_expenditures": len(e),
            "avg_contribution": round(c["amount_clean"].mean(), 2) if not c.empty else 0,
            "max_contribution": round(c["amount_clean"].max(), 2) if not c.empty else 0,
            "dc_contributions": dc_total,
            "out_of_dc_contributions": out_of_dc_total,
            "top_donors": top_donors,
            "spending_by_purpose": spending,
        })

    # Load FEC data if available
    fec_path = os.path.join(RAW_DIR, "fec_candidates.json")
    fec_candidates = []
    if os.path.exists(fec_path):
        with open(fec_path) as f:
            fec_raw = json.load(f)
        for fc in fec_raw:
            # Map FEC top_donors format to site format (keep state + employer)
            top_donors = []
            for d in fc.get("top_donors", []):
                top_donors.append({
                    "name": d.get("name", ""),
                    "total": d.get("total", 0),
                    "state": d.get("state", ""),
                    "employer": d.get("employer", ""),
                })
            # Map FEC expenditures to spending_by_purpose format
            spending = []
            for e in fc.get("top_expenditures", []):
                desc = e.get("description") or e.get("recipient", "")
                recipient = e.get("recipient", "")
                label = f"{recipient} — {desc}" if desc and recipient else (desc or recipient)
                spending.append({"purpose": label, "total": e.get("amount", 0)})

            fec_candidates.append({
                "committee_name": fc.get("name", ""),
                "candidate_name": fc.get("name", ""),
                "office": "US House (DC Delegate)",
                "party": fc.get("party", ""),
                "committee_id": fc.get("candidate_id", ""),
                "filer_type": "FEC",
                "total_raised": fc.get("receipts", 0),
                "total_spent": fc.get("disbursements", 0),
                "num_contributions": 0,
                "num_expenditures": 0,
                "cash_on_hand": fc.get("cash_on_hand", 0),
                "dc_contributions": fc.get("dc_contributions", 0),
                "out_of_dc_contributions": fc.get("out_of_dc_contributions", 0),
                "top_donors": top_donors,
                "spending_by_purpose": spending,
            })
        print(f"  FEC candidates: {len(fec_candidates)}")

    # Load Fair Elections data: payment totals + individual contributions
    fe_path = os.path.join(RAW_DIR, "fair_elections.json")
    fe_contrib_path = os.path.join(RAW_DIR, "fair_elections_contributions.csv")
    fe_expend_path = os.path.join(RAW_DIR, "fair_elections_expenditures.csv")

    fe_candidates = []
    if os.path.exists(fe_path):
        with open(fe_path) as f:
            fe_raw = json.load(f)

        # Load contribution-level data if available
        fe_contrib = pd.DataFrame()
        if os.path.exists(fe_contrib_path):
            fe_contrib = pd.read_csv(fe_contrib_path, dtype=str)
            fe_contrib["amount"] = pd.to_numeric(fe_contrib["amount"], errors="coerce").fillna(0)

        fe_expend = pd.DataFrame()
        if os.path.exists(fe_expend_path):
            fe_expend = pd.read_csv(fe_expend_path, dtype=str)
            fe_expend["amount"] = pd.to_numeric(fe_expend["amount"], errors="coerce").fillna(0)

        for fc in fe_raw:
            committee = fc.get("committee_name") or fc.get("committee", "")
            candidate = fc.get("candidate_name") or fc.get("name", "")

            # Match contributions by candidate name
            # CSV format is "Aparna Raj (Council Ward 1)" while JSON is "Aparna Raj"
            c = pd.DataFrame()
            e = pd.DataFrame()
            if not fe_contrib.empty and "candidate" in fe_contrib.columns:
                c = fe_contrib[fe_contrib["candidate"].str.startswith(candidate, na=False)]
            if not fe_expend.empty and "candidate" in fe_expend.columns:
                e = fe_expend[fe_expend["candidate"].str.startswith(candidate, na=False)]

            # Separate public funds from individual contributions
            top_donors = []
            dc_total = 0
            out_of_dc_total = 0
            individual_raised = 0
            public_funds = 0
            num_individual_donors = 0
            if not c.empty:
                # Split by contribution type
                is_public = c["contribution_type"].str.contains("Public Funds", na=False)
                is_individual = c["contribution_type"].str.contains("Individual", na=False)
                is_candidate = c["contribution_type"].str.contains("Candidate", na=False)

                public_funds = round(c[is_public]["amount"].sum(), 2)
                individuals = c[is_individual | is_candidate]
                individual_raised = round(individuals["amount"].sum(), 2)
                num_individual_donors = len(individuals)

                # Top 5 individual donors (exclude public funds)
                if not individuals.empty:
                    ind = individuals.copy()
                    ind["donor"] = (ind["first_name"].fillna("") + " " + ind["last_name"].fillna("")).str.strip()
                    ind = ind[ind["donor"] != ""]
                    top5 = ind.groupby("donor")["amount"].sum().sort_values(ascending=False).head(5)
                    top_donors = [{"name": n, "total": round(v, 2)} for n, v in top5.items()]

                # DC vs out-of-DC (individual contributions only)
                dc_total = round(individuals[individuals["state"] == "DC"]["amount"].sum(), 2)
                out_of_dc_total = round(individuals[individuals["state"] != "DC"]["amount"].sum(), 2)

            # Spending by purpose
            spending = []
            if not e.empty and "purpose" in e.columns:
                purposes = e.groupby("purpose")["amount"].sum().sort_values(ascending=False)
                spending = [{"purpose": p, "total": round(v, 2)} for p, v in purposes.head(5).items()]

            base = fc.get("base_payment") or fc.get("base_paid", 0)
            matching = fc.get("matching_payment") or fc.get("matching_paid", 0)
            total_paid = fc.get("total_paid", 0)

            fe_candidates.append({
                "committee_name": committee,
                "candidate_name": candidate,
                "office": fc.get("office", ""),
                "party": fc.get("party", ""),
                "committee_id": fc.get("committee_id") or fc.get("committee_code", ""),
                "filer_type": "Fair Elections",
                "certification_status": fc.get("certification_status", ""),
                "total_raised": float(total_paid) if total_paid else 0,
                "total_spent": round(e["amount"].sum(), 2) if not e.empty else 0,
                "base_payment": float(base) if base else 0,
                "matching_payment": float(matching) if matching else 0,
                "individual_raised": individual_raised,
                "public_funds": public_funds,
                "num_individual_donors": num_individual_donors,
                "num_contributions": len(c),
                "num_expenditures": len(e),
                "dc_contributions": dc_total,
                "out_of_dc_contributions": out_of_dc_total,
                "top_donors": top_donors,
                "spending_by_purpose": spending,
            })
        print(f"  Fair Elections candidates: {len(fe_candidates)} "
              f"({len(fe_contrib):,} contributions, {len(fe_expend):,} expenditures)")

    all_entries = committees + fec_candidates + fe_candidates
    site_data = {
        "metadata": metadata,
        "committees": all_entries,
    }
    with open(site_path, "w") as f:
        json.dump(site_data, f)
    print(f"  Site JSON: {site_path} ({len(committees)} OCF + "
          f"{len(fec_candidates)} FEC + {len(fe_candidates)} Fair Elections "
          f"= {len(all_entries)} entries)")


def save_to_sqlite(registry, contributions, expenditures, candidate_summary,
                   race_summary, donor_summary, spending):
    """Write all tables to SQLite."""
    conn = sqlite3.connect(DB_PATH)

    if not registry.empty:
        registry.to_sql("filer_registry", conn, if_exists="replace", index=False)
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
    if not race_summary.empty:
        race_summary.to_sql("race_summary", conn, if_exists="replace", index=False)
    if not donor_summary.empty:
        donor_summary.to_sql("donor_summary", conn, if_exists="replace", index=False)
    if not spending.empty:
        spending.to_sql("spending_by_purpose", conn, if_exists="replace", index=False)

    conn.close()
    print(f"SQLite database: {DB_PATH}")


def main():
    os.makedirs(PROCESSED_DIR, exist_ok=True)

    print("Loading filer registry...")
    registry = load_registry()
    lookup = build_registry_lookup(registry)
    print(f"  {len(lookup)} committees in registry")

    print("Loading raw data...")
    contributions = load_contributions()
    expenditures = load_expenditures()
    print(f"  Contributions: {len(contributions):,} records")
    print(f"  Expenditures:  {len(expenditures):,} records")

    # Enrich transaction data with race/candidate info
    if lookup:
        print("Enriching with registry data...")
        contributions = enrich_with_registry(contributions, lookup)
        expenditures = enrich_with_registry(expenditures, lookup)
        matched_c = (contributions["office"] != "").sum()
        matched_e = (expenditures["office"] != "").sum()
        print(f"  Contributions matched to a race: {matched_c:,}/{len(contributions):,}")
        print(f"  Expenditures matched to a race:  {matched_e:,}/{len(expenditures):,}")

    print()
    print("Building candidate summary...")
    candidate_summary = build_candidate_summary(contributions, expenditures, lookup)
    candidate_summary.to_csv(os.path.join(PROCESSED_DIR, "candidate_summary.csv"), index=False)
    print(f"  {len(candidate_summary)} committees")

    if not candidate_summary.empty:
        print("\n  Top 10 fundraisers:")
        for _, row in candidate_summary.head(10).iterrows():
            office = f" [{row['office']}]" if row.get("office") else ""
            print(f"    {row['Committee Name']}{office}: ${row['total_raised']:,.0f} "
                  f"({int(row['num_contributions'])} contributions)")
        print()

    print("Building race summary...")
    race_summary = build_race_summary(candidate_summary)
    race_summary.to_csv(os.path.join(PROCESSED_DIR, "race_summary.csv"), index=False)
    if not race_summary.empty:
        print("  Money by race:")
        for _, row in race_summary.iterrows():
            print(f"    {row['office']}: ${row['total_raised']:,.0f} raised, "
                  f"${row['total_spent']:,.0f} spent ({int(row['num_committees'])} committees)")
        print()

    print("Building donor summary...")
    donor_summary = build_donor_summary(contributions)
    donor_summary.to_csv(os.path.join(PROCESSED_DIR, "donor_summary.csv"), index=False)
    print(f"  {len(donor_summary):,} unique donors")

    print("Building spending by purpose...")
    spending = build_spending_by_purpose(expenditures)
    spending.to_csv(os.path.join(PROCESSED_DIR, "spending_by_purpose.csv"), index=False)

    print("Building site data JSON...")
    build_site_json(contributions, expenditures, lookup,
                    json.load(open(os.path.join(RAW_DIR, "metadata.json"))))

    print("Saving to SQLite...")
    save_to_sqlite(registry, contributions, expenditures, candidate_summary,
                   race_summary, donor_summary, spending)

    print("\nDone!")


if __name__ == "__main__":
    main()
