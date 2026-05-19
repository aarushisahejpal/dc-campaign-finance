#!/usr/bin/env python3
"""
Scrape the OCF Active Filers registry via Selenium.
Gets committee IDs, candidate names, offices, parties, and election years
from the DataTable on the e-filing search page.

Requires: selenium, Chrome/Chromium
"""

import json
import os
import time

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_FILE = os.path.join(SCRIPT_DIR, "..", "data", "raw", "filer_registry.json")

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
    "19": "Senators & Representatives",
}


def main():
    print("DC OCF Filer Registry scraper (Selenium)")
    print("=" * 50)

    opts = Options()
    opts.add_argument("--headless")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")

    driver = webdriver.Chrome(options=opts)
    driver.get("https://efiling.ocf.dc.gov/ContributionExpenditure")
    time.sleep(4)
    print(f"Page loaded: {driver.title}\n")

    all_filers = []

    for fid, fname in FILER_TYPES.items():
        try:
            select = Select(driver.find_element(By.ID, "FilerTypeId"))
            select.select_by_value(fid)
            time.sleep(4)

            # Show all entries via DataTable API
            driver.execute_script(
                'jQuery("#RecepientSearch").DataTable().page.len(-1).draw();'
            )
            time.sleep(3)

            # Extract data via DataTable API
            data = driver.execute_script(
                'var t = jQuery("#RecepientSearch").DataTable();'
                "return JSON.stringify(t.rows().data().toArray());"
            )
            records = json.loads(data)
            for r in records:
                r["FilerType"] = fname
            all_filers.extend(records)
            print(f"  {fname}: {len(records)} filers")
        except Exception as e:
            print(f"  {fname}: ERROR - {e}")
        time.sleep(2)

    driver.quit()

    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    with open(OUTPUT_FILE, "w") as f:
        json.dump(all_filers, f, indent=2)

    print(f"\n{len(all_filers)} total filers saved to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
