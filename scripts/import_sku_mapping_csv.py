"""
Load the ops team's "SAP FILLING SHEET - MAPPING.csv" into SKU Mapping via
POST /api/master-data/sku-mappings/sync — the same path SAP will use, so the
load is idempotent and every row is validated fail-loud against Item Master.

Column mapping (CSV -> SKU_Mapping):
    CHAIN          -> partner_code   (RELIANCE -> RELIANCE_JIO; must exist as a partner)
    ITEM CODE      -> buyer_sku      (the retailer's own code: EAN-ish, UUID, ASIN, FSN…)
    SAP ITEM CODE  -> b1_item_code   (must exist in Item Master — rejected otherwise)
    ITEM NAME      -> item_name      (the retailer's description)
    UNIT COST      -> unit_price     (0.00/blank treated as unknown -> omitted, so
                                      PriceVarianceRule doesn't compare POs against 0)
    Discount       -> margin         (blank -> omitted)

Skipped, by design:
    rows with no CHAIN            (EAN-keyed reference rows + blank filler lines)
    rows with SAP ITEM CODE #N/A  (retailer listings not yet mapped in SAP)
    SAP ALTERNATE CODE            (no field for it — reported, not silently dropped)

Usage:
    python scripts/import_sku_mapping_csv.py "/path/to/MAPPING.csv"
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import urllib.request

_CHAIN_TO_PARTNER = {
    "BLINKIT": "BLINKIT",
    "ZEPTO": "ZEPTO",
    "SWIGGY": "SWIGGY",
    "FLIPKART": "FLIPKART",
    "BIGBASKET": "BIGBASKET",
    "AMAZON": "AMAZON",
    "RELIANCE": "RELIANCE_JIO",
    "LOTS": "LOTS",
}


def build_mappings(csv_path: str) -> tuple[list[dict], dict[str, int], list[str]]:
    mappings: list[dict] = []
    skipped = {"no_chain": 0, "no_item_code": 0, "sap_na": 0, "unknown_chain": 0}
    alt_codes: list[str] = []

    with open(csv_path, newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            chain = (row.get("CHAIN") or "").strip().upper()
            buyer_sku = (row.get("ITEM CODE") or "").strip()
            sap_code = (row.get("SAP ITEM CODE") or "").strip()

            if not chain:
                skipped["no_chain"] += 1
                continue
            if chain not in _CHAIN_TO_PARTNER:
                skipped["unknown_chain"] += 1
                continue
            if not buyer_sku:
                skipped["no_item_code"] += 1
                continue
            if not sap_code or sap_code == "#N/A":
                skipped["sap_na"] += 1
                continue

            unit_cost = (row.get("UNIT COST") or "").strip()
            discount = (row.get("Discount") or "").strip()
            alt = (row.get("SAP ALTERNATE CODE") or "").strip()
            if alt:
                alt_codes.append(f"{chain}/{buyer_sku}: alt {alt}")

            m: dict = {
                "partner_code": _CHAIN_TO_PARTNER[chain],
                "buyer_sku": buyer_sku,
                "b1_item_code": sap_code,
                "item_name": (row.get("ITEM NAME") or "").strip() or None,
                "status": True,
            }
            # 0.00 means "not negotiated yet", not a real price — omit rather than
            # store a zero that PriceVarianceRule would flag every PO against.
            if unit_cost and float(unit_cost) > 0:
                m["unit_price"] = unit_cost
            if discount:
                m["margin"] = discount
            mappings.append(m)

    return mappings, skipped, alt_codes


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("csv_path")
    ap.add_argument("--base-url", default="http://localhost:8000")
    ap.add_argument("--email", default="tech@letstryfoods.com")
    ap.add_argument("--password", default="TestPass123!")
    args = ap.parse_args()

    mappings, skipped, alt_codes = build_mappings(args.csv_path)
    print(f"parsed {len(mappings)} loadable mappings")
    print(f"skipped: {skipped}")
    if alt_codes:
        print(f"SAP ALTERNATE CODE not stored for {len(alt_codes)} rows (no field)")

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
    for start in range(0, len(mappings), 2000):
        batch = mappings[start:start + 2000]
        req = urllib.request.Request(
            f"{base}/api/master-data/sku-mappings/sync",
            data=json.dumps({"mappings": batch}).encode(),
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
    for e in errors[:15]:
        print(f"  rejected: {e}")
    if len(errors) > 15:
        print(f"  … and {len(errors) - 15} more")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
