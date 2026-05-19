#!/usr/bin/env python3
"""
DC Office of Campaign Finance scraper.
Pulls contributions and expenditures via the CSV export endpoint
at efiling.ocf.dc.gov for all filer types tagged to the 2026 election.
"""

import csv
import io
import json
import os
import re
import time
from datetime import date

import requests

BASE_URL = "https://efiling.ocf.dc.gov/ContributionExpenditure"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
RAW_DIR = os.path.join(SCRIPT_DIR, "..", "data", "raw")

ELECTION_YEAR = "2026"

# All filer types on the OCF site
FILER_TYPES = {
    "2": "Principal Campaign Committee",
    "3": "Political Action Committee",
    "4": "Initiative",
    "5": "Recall",
    "6": "Referendum",
    "7": "Exploratory Committee",
    "8": "Constituent Service Program",
    "11": "Transition Committee",
    "12": "Inaugural Committee",
    "13": "Legal Defense Committee",
    "14": "Independent Expenditure Committee",
    "15": "Advisory Neighborhood Commission",
    "19": "Senators & Representatives",
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
}


def get_session_and_token():
    """Start a session and grab the anti-forgery token."""
    session = requests.Session()
    session.headers.update(HEADERS)
    r = session.get(BASE_URL, timeout=30)
    r.raise_for_status()
    match = re.search(
        r'__RequestVerificationToken.*?value="([^"]+)"', r.text
    )
    if not match:
        raise RuntimeError("Could not find verification token")
    return session, match.group(1)


def scrape_csv(session, token, filer_type_id, search_type, election_year=None):
    """Submit a search and download the CSV export.

    Returns decoded CSV text (may be empty if no results).
    """
    data = {
        "__RequestVerificationToken": token,
        "FilerTypeId": filer_type_id,
        "SearchType": search_type,
    }
    if election_year:
        data["ElectionYear"] = election_year

    # Submit search to set server-side session state
    r = session.post(
        f"{BASE_URL}/SubmitSearch",
        data=data,
        timeout=30,
        headers={"Referer": BASE_URL},
    )
    r.raise_for_status()

    # Download CSV export (server returns UTF-16 encoded CSV)
    r_csv = session.get(
        f"{BASE_URL}/Export?exportType=CSV",
        timeout=120,
        headers={"Referer": f"{BASE_URL}/SearchResults"},
    )
    r_csv.raise_for_status()

    if not r_csv.content:
        return ""

    return r_csv.content.decode("utf-16")


def parse_csv_text(csv_text):
    """Parse the OCF CSV text into a list of dicts.

    The first line is a title row (e.g. 'Principal Campaign Committee  Contributions Report').
    The second line is the actual header row.
    """
    lines = csv_text.strip().split("\n")
    if len(lines) < 3:
        return []

    # Skip the title line, parse from the header row
    reader = csv.DictReader(io.StringIO("\n".join(lines[1:])))
    return list(reader)


def scrape_all(election_year=ELECTION_YEAR):
    """Scrape contributions and expenditures for all filer types
    tagged to a given election year.
    """
    os.makedirs(RAW_DIR, exist_ok=True)
    session, token = get_session_and_token()

    for search_type in ["Contributions", "Expenditures"]:
        print(f"Scraping {search_type.lower()}...")
        all_rows = []
        for filer_id, filer_name in FILER_TYPES.items():
            print(f"  {filer_name} ({filer_id})...", end=" ", flush=True)

            try:
                csv_text = scrape_csv(
                    session, token, filer_id, search_type,
                    election_year=election_year,
                )
                rows = parse_csv_text(csv_text)
                for row in rows:
                    row["Filer Type"] = filer_name
                all_rows.extend(rows)
                print(f"{len(rows):,} records")
            except Exception as e:
                print(f"ERROR: {e}")

            time.sleep(1)

        if all_rows:
            out_file = os.path.join(RAW_DIR, f"{search_type.lower()}.csv")
            # Collect all unique fieldnames across filer types
            all_fields = []
            seen = set()
            for row in all_rows:
                for k in row.keys():
                    if k not in seen:
                        all_fields.append(k)
                        seen.add(k)
            with open(out_file, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=all_fields, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(all_rows)
            print(f"\n  -> {len(all_rows):,} total {search_type.lower()} saved to {out_file}")
        else:
            print(f"\n  -> No {search_type.lower()} found")

        print()

    return all_rows is not None


def write_metadata(election_year, contrib_count, expend_count):
    """Write a metadata file with scrape timestamp and counts."""
    meta_path = os.path.join(RAW_DIR, "metadata.json")
    meta = {
        "election_year": election_year,
        "last_updated": date.today().isoformat(),
        "scrape_timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "contributions_count": contrib_count,
        "expenditures_count": expend_count,
    }
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2)
    print(f"Metadata: {meta_path}")


def main():
    """Scrape 2026 DC election data."""
    print("DC Campaign Finance scraper")
    print(f"Election year: {ELECTION_YEAR}")
    print("=" * 50)
    print()

    scrape_all(election_year=ELECTION_YEAR)

    # Count results
    contrib_path = os.path.join(RAW_DIR, "contributions.csv")
    expend_path = os.path.join(RAW_DIR, "expenditures.csv")
    c_count = sum(1 for _ in open(contrib_path)) - 1 if os.path.exists(contrib_path) else 0
    e_count = sum(1 for _ in open(expend_path)) - 1 if os.path.exists(expend_path) else 0

    write_metadata(ELECTION_YEAR, c_count, e_count)
    print("Done!")


if __name__ == "__main__":
    main()
