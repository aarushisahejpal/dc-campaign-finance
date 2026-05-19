#!/usr/bin/env python3
"""
Scrape DC Fair Elections Program data via Selenium.
Iterates through registrant IDs on fairelections.ocf.dc.gov
to get candidate info and payment details.
"""

import json
import os
import re
import time

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_FILE = os.path.join(SCRIPT_DIR, "..", "data", "raw", "fair_elections.json")
BASE_URL = "https://fairelections.ocf.dc.gov/public/registrantDisclosureDetails"

# ID range to scan — covers all known 2026 registrants
ID_START = 80
ID_END = 250


def parse_page(driver):
    """Parse a Fair Elections registrant detail page."""
    body = driver.find_element(By.TAG_NAME, "body").text
    if "Fair Elections Registrant Disclosure Details" not in body:
        return None

    lines = body.split("\n")
    info = {
        "name": "",
        "committee": "",
        "office": "",
        "party": "",
        "certification_status": "",
        "registration_date": "",
        "election": "",
        "committee_id": "",
        "base_paid": 0,
        "matching_paid": 0,
        "total_paid": 0,
        "payments": [],
    }

    # Parse name / committee from the line after the heading
    for i, line in enumerate(lines):
        if "Fair Elections Registrant Disclosure Details" in line:
            for j in range(i + 1, min(i + 5, len(lines))):
                if lines[j].strip() and "Registration" not in lines[j]:
                    parts = lines[j].strip().split(" / ")
                    if len(parts) == 2:
                        info["name"] = parts[0].strip()
                        info["committee"] = parts[1].strip()
                    else:
                        info["name"] = lines[j].strip()
                    break
            break

    for line in lines:
        if "Office Sought:" in line:
            info["office"] = line.replace("Office Sought:", "").strip()
        elif "Party Affiliation:" in line:
            info["party"] = line.replace("Party Affiliation:", "").strip()
        elif "Certification Status:" in line:
            info["certification_status"] = line.replace("Certification Status:", "").strip()
        elif "Registration Date:" in line:
            info["registration_date"] = line.replace("Registration Date:", "").strip()
        elif "Registered for Election:" in line:
            info["election"] = line.replace("Registered for Election:", "").strip()
        elif "Committee ID:" in line:
            info["committee_id"] = line.replace("Committee ID:", "").strip()

    # Parse payment amounts from the Fair Election Payouts table
    # The table has: Payment Type | Payment Made | Payment Ceiling
    # Then: Base Amount | $X | $Y
    #        Matching Amount | $X | $Y
    #        Total Amount | $X | $Y
    amounts = re.findall(r"\$\s*([\d,]+\.?\d*)", body)
    amounts_float = []
    for a in amounts:
        try:
            amounts_float.append(float(a.replace(",", "")))
        except ValueError:
            pass

    # The payments section has pairs of amounts (paid, ceiling)
    # Look for the pattern in the text
    # The payout summary shows "Base Amount $ X $ Y" (paid then ceiling)
    # and "Matching Amount $ X $ Y" and "Total Amount $ X $ Y"
    base_match = re.search(r"Base Amount\s*\$?\s*([\d,]+\.\d{2})", body)
    matching_match = re.search(r"Matching Amount\s*\$?\s*([\d,]+\.\d{2})", body)
    total_match = re.search(r"Total Amount\s*\$?\s*([\d,]+\.\d{2})", body)

    if base_match:
        info["base_paid"] = float(base_match.group(1).replace(",", ""))
    if matching_match:
        info["matching_paid"] = float(matching_match.group(1).replace(",", ""))
    if total_match:
        info["total_paid"] = float(total_match.group(1).replace(",", ""))

    # Parse individual payment rows from the Payments History section
    # Format: MM/DD/YYYY $ amount Type N/A MM/DD/YYYY
    payment_pattern = re.findall(
        r"(\d{2}/\d{2}/\d{4})\s*\$?\s*([\d,]+\.\d{2})\s*(Base Amount|Matching Amount)",
        body,
    )
    for date, amount, ptype in payment_pattern:
        info["payments"].append({
            "date": date,
            "amount": float(amount.replace(",", "")),
            "type": ptype,
        })

    # Cross-check: if total_paid is 0 but we found payments, use the sum
    payment_sum = sum(p["amount"] for p in info["payments"])
    if info["total_paid"] == 0 and payment_sum > 0:
        info["total_paid"] = payment_sum
        info["_total_from_payments"] = True

    return info


def main():
    print("DC Fair Elections Program scraper")
    print("=" * 50)

    opts = Options()
    opts.add_argument("--headless")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")

    driver = webdriver.Chrome(options=opts)

    candidates = []
    for rid in range(ID_START, ID_END + 1):
        driver.get(f"{BASE_URL}/{rid}")
        time.sleep(3)

        info = parse_page(driver)
        if info is None:
            continue

        # Only keep 2026 primary candidates
        if "2026" not in info.get("election", ""):
            continue

        info["registrant_id"] = rid
        candidates.append(info)
        status_label = info["certification_status"]
        print(f"  ID {rid}: {info['name']} | {info['office']} | "
              f"{info['party']} | {status_label} | "
              f"${info['total_paid']:,.0f} paid")

    driver.quit()

    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    with open(OUTPUT_FILE, "w") as f:
        json.dump(candidates, f, indent=2)

    print(f"\n{len(candidates)} Fair Elections candidates saved to {OUTPUT_FILE}")

    total_paid = sum(c["total_paid"] for c in candidates)
    print(f"Total public financing: ${total_paid:,.0f}")


if __name__ == "__main__":
    main()
