#!/usr/bin/env python3
"""Dedupe Customer records in Accu360 by phone number.

Groups customers by the last-9 digits of their phone (extracted from
customer_name, mobile_no, or mobile_number). For each group with more
than one record, keeps the newest (by creation date) as canonical and
merges the older ones into it via Frappe's rename_doc(merge=True),
which re-points all linked Sales Orders, Addresses, and Contacts to
the canonical record, then deletes the duplicate.

Usage:
    python dedupe_customers.py             # dry-run, prints what would happen
    python dedupe_customers.py --apply     # actually merge

Reads ACCU360_API_KEY / ACCU360_API_SECRET / ACCU360_API_BASE_URL from
the environment (or .env in the same directory).
"""

import os
import re
import sys
import argparse
import httpx
from dotenv import load_dotenv

load_dotenv()

ACCU360_API_KEY = os.getenv("ACCU360_API_KEY")
ACCU360_API_SECRET = os.getenv("ACCU360_API_SECRET")
ACCU360_API_BASE_URL = os.getenv("ACCU360_API_BASE_URL")

if not all([ACCU360_API_KEY, ACCU360_API_SECRET, ACCU360_API_BASE_URL]):
    print(
        "ERROR: Missing ACCU360_API_KEY / ACCU360_API_SECRET / ACCU360_API_BASE_URL.\n"
        "Set them in your environment or in a .env file in this directory.",
        file=sys.stderr,
    )
    sys.exit(1)

HEADERS = {
    "Authorization": f"token {ACCU360_API_KEY}:{ACCU360_API_SECRET}",
    "Content-Type": "application/json",
    "Accept": "application/json",
}


def normalize(phone):
    """Strip non-digit characters from a phone string."""
    if not phone:
        return ""
    return re.sub(r"\D", "", phone)


def last9(phone):
    """Return the last 9 digits of a phone, or '' if fewer than 9 digits."""
    d = normalize(phone)
    return d[-9:] if len(d) >= 9 else ""


def extract_phone_key(customer):
    """Pick the first non-empty phone field and return its last-9 digits.
    Returns '' if no field has a recognisable phone."""
    for field in ("customer_name", "mobile_no", "mobile_number"):
        key = last9(customer.get(field))
        if key:
            return key
    return ""


def fetch_all_customers(client):
    """Fetch every Customer record, paginating through Frappe's REST API."""
    customers = []
    start = 0
    page_size = 100
    while True:
        resp = client.get(
            f"{ACCU360_API_BASE_URL}/api/resource/Customer",
            headers=HEADERS,
            params={
                "fields": (
                    '["name","customer_name","customer_full_name",'
                    '"mobile_no","mobile_number","creation"]'
                ),
                "limit_start": start,
                "limit_page_length": page_size,
                "order_by": "creation asc",
            },
            timeout=60.0,
        )
        resp.raise_for_status()
        batch = resp.json().get("data", []) or []
        customers.extend(batch)
        if len(batch) < page_size:
            break
        start += page_size
    return customers


def merge_customer(client, old_name, new_name):
    """Call Frappe's rename_doc with merge=1 to fold `old_name` into `new_name`.
    All linked transactions get re-pointed and the old Customer is deleted.
    Returns the httpx.Response so callers can inspect status/body."""
    return client.post(
        f"{ACCU360_API_BASE_URL}/api/method/frappe.client.rename_doc",
        headers=HEADERS,
        json={
            "doctype": "Customer",
            "old": old_name,
            "new": new_name,
            "merge": 1,
        },
        timeout=120.0,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually perform merges (default is dry-run).",
    )
    args = parser.parse_args()

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"Mode: {mode}")
    print(f"Base URL: {ACCU360_API_BASE_URL}\n")

    with httpx.Client() as client:
        print("Fetching all customers ...")
        customers = fetch_all_customers(client)
        print(f"Fetched {len(customers)} customers.\n")

        groups = {}
        skipped_no_phone = 0
        for c in customers:
            key = extract_phone_key(c)
            if not key:
                skipped_no_phone += 1
                continue
            groups.setdefault(key, []).append(c)

        duplicates = {k: v for k, v in groups.items() if len(v) > 1}
        print(f"{len(groups)} unique phone keys.")
        print(f"{skipped_no_phone} customers had no recognisable phone (skipped).")
        print(f"{len(duplicates)} phone keys have duplicates.\n")

        if not duplicates:
            print("Nothing to do.")
            return

        merged = 0
        failed = 0
        for last9_key, group in duplicates.items():
            # Newest first — by creation date desc.
            group.sort(key=lambda c: c.get("creation") or "", reverse=True)
            canonical = group[0]
            dups = group[1:]
            print(
                f"Phone ...{last9_key}: keeping {canonical['name']} "
                f"(created {canonical.get('creation', '?')}, "
                f"customer_name={canonical.get('customer_name', '?')!r})"
            )
            for dup in dups:
                arrow = "would merge" if not args.apply else "merging"
                print(
                    f"  - {arrow} {dup['name']} "
                    f"(created {dup.get('creation', '?')}, "
                    f"customer_name={dup.get('customer_name', '?')!r}) "
                    f"-> {canonical['name']}"
                )
                if args.apply:
                    try:
                        resp = merge_customer(client, dup["name"], canonical["name"])
                        if resp.status_code == 200:
                            print("    [ok] merged")
                            merged += 1
                        else:
                            print(f"    [fail] failed [{resp.status_code}]: {resp.text[:300]}")
                            failed += 1
                    except Exception as e:
                        print(f"    [fail] exception: {e}")
                        failed += 1
            print()

        if args.apply:
            print(f"Done. Merged: {merged}, Failed: {failed}")
        else:
            print("Dry-run complete. Re-run with --apply to perform the merges.")


if __name__ == "__main__":
    main()
