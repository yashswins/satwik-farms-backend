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
import csv
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


def keep_priority(customer):
    """Rank within a duplicate group: prefer cleanest customer_name format.

    3 = customer_name starts with '+' (full E.164, e.g. '+255678406696')
    2 = customer_name is digits-only (e.g. '0678406696', '255678406696')
    1 = customer_name contains letters (it's a person/business name)
    0 = customer_name is empty
    """
    name = (customer.get("customer_name") or "").strip()
    if not name:
        return 0
    has_letters = bool(re.search(r"[A-Za-z]", name))
    if name.startswith("+") and not has_letters:
        return 3
    if not has_letters:
        return 2
    return 1


def sort_key(customer):
    """Sort key for picking the canonical record in a duplicate group.
    Sorting reverse=True with this key gives: highest priority first,
    then newest creation as tie-break."""
    return (keep_priority(customer), customer.get("creation") or "")


def is_text_only(customer):
    """True if customer_name contains letters (i.e. it's a name, not a phone).
    These are test records left over from earlier app-buggy code paths and
    get deleted outright by the script."""
    name = (customer.get("customer_name") or "").strip()
    return bool(name) and bool(re.search(r"[A-Za-z]", name))


def delete_customer(client, name):
    """Hard-delete a Customer via REST. Frappe will refuse if any
    submitted/linked transactions reference it; caller inspects status."""
    return client.delete(
        f"{ACCU360_API_BASE_URL}/api/resource/Customer/{name}",
        headers=HEADERS,
        timeout=60.0,
    )


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
    parser.add_argument(
        "--csv",
        metavar="PATH",
        help="Write every duplicate group to a CSV at PATH for review.",
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

        if args.csv:
            with open(args.csv, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "phone_last9",
                    "action",
                    "customer_id",
                    "customer_name",
                    "customer_full_name",
                    "mobile_no",
                    "mobile_number",
                    "creation",
                    "merge_target",
                ])
                # Track which records get merged in pass 1 so we don't
                # double-list them as deletes in pass 2 below.
                merge_planned = set()
                for last9_key, group in sorted(duplicates.items()):
                    sorted_group = sorted(group, key=sort_key, reverse=True)
                    canonical = sorted_group[0]
                    if is_text_only(canonical):
                        # All-text-only group — pass 1 will skip; all rows
                        # get listed under "delete" below.
                        continue
                    writer.writerow([
                        last9_key,
                        "keep",
                        canonical.get("name", ""),
                        canonical.get("customer_name", ""),
                        canonical.get("customer_full_name", ""),
                        canonical.get("mobile_no", ""),
                        canonical.get("mobile_number", ""),
                        canonical.get("creation", ""),
                        "",
                    ])
                    for dup in sorted_group[1:]:
                        merge_planned.add(dup["name"])
                        writer.writerow([
                            last9_key,
                            "merge",
                            dup.get("name", ""),
                            dup.get("customer_name", ""),
                            dup.get("customer_full_name", ""),
                            dup.get("mobile_no", ""),
                            dup.get("mobile_number", ""),
                            dup.get("creation", ""),
                            canonical.get("name", ""),
                        ])

                # Append "delete" rows for every text-only record that
                # won't be absorbed by a merge (i.e. test data).
                for c in customers:
                    if c["name"] in merge_planned:
                        continue
                    if not is_text_only(c):
                        continue
                    writer.writerow([
                        extract_phone_key(c),
                        "delete",
                        c.get("name", ""),
                        c.get("customer_name", ""),
                        c.get("customer_full_name", ""),
                        c.get("mobile_no", ""),
                        c.get("mobile_number", ""),
                        c.get("creation", ""),
                        "",
                    ])
            print(f"Wrote duplicate report to {args.csv}\n")

        # ── PASS 1: merge duplicates that have a non-text-only canonical.
        #    Groups where every member has a text-only customer_name are
        #    skipped here and handled entirely by pass 2 (delete).
        print("=== Pass 1: merging duplicates ===\n")
        merged = 0
        merge_failed = 0
        merged_names = set()
        for last9_key, group in duplicates.items():
            # Sort: cleanest customer_name format first, newest as tie-breaker.
            group.sort(key=sort_key, reverse=True)
            canonical = group[0]
            if is_text_only(canonical):
                # Whole group is test/text-only — skip merging, pass 2 deletes them.
                continue
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
                            merged_names.add(dup["name"])
                        else:
                            print(f"    [fail] [{resp.status_code}]: {resp.text[:300]}")
                            merge_failed += 1
                    except Exception as e:
                        print(f"    [fail] exception: {e}")
                        merge_failed += 1
                else:
                    merged_names.add(dup["name"])  # for pass 2's "remaining" calc
            print()

        # ── PASS 2: delete every customer whose customer_name is text-only
        #    and that wasn't already merged-away in pass 1. These are the
        #    leftover test records (Naman Test, Abhishek Kumar, Vinod Varma,
        #    etc.) the user wants gone. Frappe will refuse the DELETE if
        #    submitted Sales Orders / Invoices reference the customer; those
        #    failures get printed for manual cleanup.
        print("=== Pass 2: deleting remaining text-only records ===\n")
        text_only_to_delete = [
            c for c in customers
            if c["name"] not in merged_names and is_text_only(c)
        ]
        print(f"{len(text_only_to_delete)} text-only customer_name records remain after pass 1.\n")
        deleted = 0
        delete_failed = 0
        for c in text_only_to_delete:
            arrow = "would delete" if not args.apply else "deleting"
            print(
                f"  - {arrow} {c['name']} "
                f"customer_name={c.get('customer_name', '?')!r} "
                f"customer_full_name={c.get('customer_full_name', '?')!r}"
            )
            if args.apply:
                try:
                    resp = delete_customer(client, c["name"])
                    if resp.status_code in (200, 202):
                        print("    [ok] deleted")
                        deleted += 1
                    else:
                        print(f"    [fail] [{resp.status_code}]: {resp.text[:300]}")
                        delete_failed += 1
                except Exception as e:
                    print(f"    [fail] exception: {e}")
                    delete_failed += 1

        print()
        if args.apply:
            print(
                f"Done. Merged: {merged} (failed: {merge_failed}). "
                f"Deleted: {deleted} (failed: {delete_failed})."
            )
        else:
            print("Dry-run complete. Re-run with --apply to perform the merges and deletes.")


if __name__ == "__main__":
    main()
