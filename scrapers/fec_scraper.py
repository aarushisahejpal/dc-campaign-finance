#!/usr/bin/env python3
"""
Scrape FEC data for DC federal candidates (2026 cycle).
Uses the FEC API to pull candidate totals for DC House races.
"""

import csv
import json
import os
import time
from datetime import date

import requests

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
RAW_DIR = os.path.join(SCRIPT_DIR, "..", "data", "raw")
FEC_API_KEY = os.environ.get("FEC_API_KEY", "DEMO_KEY")
FEC_BASE = "https://api.open.fec.gov/v1"


def get_dc_candidates(election_year=2026):
    """Get all DC federal candidates for a given election year."""
    candidates = []
    page = 1
    while True:
        r = requests.get(f"{FEC_BASE}/candidates/search/", params={
            "api_key": FEC_API_KEY,
            "state": "DC",
            "election_year": election_year,
            "per_page": 100,
            "page": page,
        }, timeout=30)
        r.raise_for_status()
        data = r.json()
        candidates.extend(data["results"])
        if page >= data["pagination"]["pages"]:
            break
        page += 1
        time.sleep(0.5)
    return candidates


def get_candidate_totals(election_year=2026):
    """Get financial totals for DC federal candidates."""
    totals = []
    for office in ["H", "S", "P"]:
        page = 1
        while True:
            r = requests.get(f"{FEC_BASE}/candidates/totals/", params={
                "api_key": FEC_API_KEY,
                "state": "DC",
                "election_year": election_year,
                "office": office,
                "per_page": 100,
                "page": page,
                "sort": "-receipts",
            }, timeout=30)
            r.raise_for_status()
            data = r.json()
            totals.extend(data["results"])
            if page >= data["pagination"]["pages"]:
                break
            page += 1
            time.sleep(0.5)
    return totals


def get_candidate_contributions(candidate_id):
    """Get Schedule A (contributions) for a candidate's principal committee."""
    # First get the candidate's principal committee
    r = requests.get(f"{FEC_BASE}/candidate/{candidate_id}/committees/", params={
        "api_key": FEC_API_KEY,
        "designation": "P",  # principal committee
    }, timeout=30)
    r.raise_for_status()
    committees = r.json()["results"]
    if not committees:
        return []

    committee_id = committees[0]["committee_id"]

    # Get contributions
    contributions = []
    last_index = None
    last_amount = None
    while True:
        params = {
            "api_key": FEC_API_KEY,
            "committee_id": committee_id,
            "two_year_transaction_period": 2026,
            "per_page": 100,
            "sort": "-contribution_receipt_amount",
        }
        if last_index:
            params["last_index"] = last_index
            params["last_contribution_receipt_amount"] = last_amount

        r = requests.get(f"{FEC_BASE}/schedules/schedule_a/", params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
        results = data["results"]
        if not results:
            break
        contributions.extend(results)
        pagination = data["pagination"]
        last_index = pagination.get("last_indexes", {}).get("last_index")
        last_amount = pagination.get("last_indexes", {}).get("last_contribution_receipt_amount")
        if not last_index:
            break
        time.sleep(0.5)

    return contributions


def main():
    print("FEC DC Federal Candidates scraper")
    print("=" * 50)
    print()

    # Get candidate list and totals
    print("Fetching DC 2026 candidates...")
    candidates = get_dc_candidates(2026)
    print(f"  {len(candidates)} candidates found")

    print("Fetching financial totals...")
    totals = get_candidate_totals(2026)
    print(f"  {len(totals)} candidates with totals")

    # Build lookup by candidate_id
    totals_lookup = {t["candidate_id"]: t for t in totals}

    # Merge and save
    os.makedirs(RAW_DIR, exist_ok=True)

    fec_candidates = []
    for c in candidates:
        t = totals_lookup.get(c["candidate_id"], {})
        fec_candidates.append({
            "candidate_id": c["candidate_id"],
            "name": c["name"],
            "office": c["office_full"],
            "district": c.get("district", ""),
            "party": c["party_full"],
            "incumbent_challenge": c.get("incumbent_challenge_full", ""),
            "receipts": float(t.get("receipts") or 0),
            "disbursements": float(t.get("disbursements") or 0),
            "cash_on_hand": float(t.get("cash_on_hand_end_period") or 0),
            "individual_contributions": float(t.get("individual_itemized_contributions") or 0),
            "committee_id": (c.get("principal_committees") or [{}])[0].get("committee_id", "")
                if c.get("principal_committees") else "",
        })

    # Sort by receipts
    fec_candidates.sort(key=lambda x: -x["receipts"])

    # Save JSON
    fec_path = os.path.join(RAW_DIR, "fec_candidates.json")
    with open(fec_path, "w") as f:
        json.dump(fec_candidates, f, indent=2)
    print(f"\nSaved {len(fec_candidates)} candidates to {fec_path}")

    # Also save as CSV
    csv_path = os.path.join(RAW_DIR, "fec_candidates.csv")
    if fec_candidates:
        with open(csv_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fec_candidates[0].keys())
            writer.writeheader()
            writer.writerows(fec_candidates)
        print(f"Saved CSV to {csv_path}")

    print("\nTop fundraisers:")
    for c in fec_candidates[:10]:
        if c["receipts"] > 0:
            print(f"  {c['name']} ({c['party']}) — ${c['receipts']:,.0f} raised, "
                  f"${c['disbursements']:,.0f} spent")

    print("\nDone!")


if __name__ == "__main__":
    main()
