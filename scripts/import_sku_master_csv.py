"""
Load the ops team's "Master Sheet - SKU MASTER.csv" into Item Master via the
sync API (POST /api/master-data/materials/sync) — the same path SAP uses, so the
load is idempotent: re-running with an updated sheet updates rows in place.

Column mapping (CSV -> Item_master):
    SAP ID            -> item_code        (required — rows without one are skipped;
                                           in the current sheet all such rows are
                                           INACTIVE discontinued lines)
    SKU SAP NAME      -> item_name
    SKU INTERNAL NAME -> frgn_name        (the internal/alternate name)
    CATEGORY          -> items_group_name
    GRAMMAGE (g)      -> grammage         (stored as "<n>g")
    CASE SIZE         -> case_size
    MRP (Rs.)         -> mrp
    EAN               -> ean_code         (string — leading zeros preserved)
    HSN               -> hsn
    Status            -> INACTIVE => is_active=false, valid_for=0; else true/1

Not loaded (no Item_master column): SHELF LIFE (Day), SKU IMAGE.

Usage:
    python scripts/import_sku_master_csv.py "/path/to/Master Sheet - SKU MASTER.csv"
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import urllib.request


def build_items(csv_path: str) -> tuple[list[dict], list[str]]:
    items: list[dict] = []
    skipped: list[str] = []
    with open(csv_path, newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            code = (row.get("SAP ID") or "").strip()
            name = (row.get("SKU SAP NAME") or "").strip()
            if not code or not name:
                skipped.append((row.get("SKU INTERNAL NAME") or "?").strip())
                continue

            active = (row.get("Status") or "").strip().upper() != "INACTIVE"

            def _num(key: str, row=row):
                v = (row.get(key) or "").strip()
                return v if v else None

            grammage = _num("GRAMMAGE (g)")
            items.append({
                "item_code": code,
                "item_name": name,
                "frgn_name": (row.get("SKU INTERNAL NAME") or "").strip() or None,
                "items_group_name": (row.get("CATEGORY") or "").strip() or None,
                "grammage": f"{grammage}g" if grammage else None,
                "case_size": int(_num("CASE SIZE")) if _num("CASE SIZE") else None,
                "mrp": _num("MRP (Rs.)"),
                "ean_code": _num("EAN"),
                "hsn": _num("HSN"),
                "invntry_uom": "PCS",
                "valid_for": 1 if active else 0,
                "is_active": active,
            })
    return items, skipped


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("csv_path")
    ap.add_argument("--base-url", default="http://localhost:8000")
    ap.add_argument("--email", default="tech@letstryfoods.com")
    ap.add_argument("--password", default="TestPass123!")
    args = ap.parse_args()

    items, skipped = build_items(args.csv_path)
    print(f"parsed {len(items)} loadable items; {len(skipped)} rows skipped (no SAP ID / SAP name)")

    base = args.base_url.rstrip("/")
    req = urllib.request.Request(
        f"{base}/auth/login",
        data=json.dumps({"email": args.email, "password": args.password}).encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        token = json.loads(resp.read())["access_token"]

    total = {"created": 0, "updated": 0, "skipped": 0}
    errors: list[str] = []
    for start in range(0, len(items), 2000):          # sync batch limit
        batch = items[start:start + 2000]
        req = urllib.request.Request(
            f"{base}/api/master-data/materials/sync",
            data=json.dumps({"items": batch}).encode(),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {token}"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=300) as resp:
            result = json.loads(resp.read())
        for k in total:
            total[k] += result.get(k, 0)
        errors.extend(result.get("errors", []))

    print(f"sync result: created={total['created']} updated={total['updated']} skipped={total['skipped']}")
    for e in errors[:10]:
        print(f"  rejected: {e}")
    if skipped:
        print(f"not sent ({len(skipped)} rows without SAP ID — all discontinued/INACTIVE):")
        for name in skipped[:5]:
            print(f"  - {name}")
        if len(skipped) > 5:
            print(f"  … and {len(skipped) - 5} more")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
