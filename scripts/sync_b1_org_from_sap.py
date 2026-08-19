"""
Bootstrap Branch Master and Warehouse Master from the live B1 company.

Master data normally travels SAP → middleware by push (CLAUDE.md section 7: Service
Layer sessions are licensed and capped, so we do not poll). Branches and warehouses are
the pragmatic exception for a *one-time* load: there are tens of rows, they change a few
times a year, and someone has to put them there before the first Sales Order can name a
branch. This is a bootstrap and reconciliation tool, not a scheduled job.

It reads B1 and then posts through our own sync endpoints rather than writing to the
database, so the same validation, ordering rule and audit log apply as when SAP pushes.

    GET  /BusinessPlaces  →  POST /api/master-data/branches/sync
    GET  /Warehouses      →  POST /api/master-data/warehouses/sync

Rows present locally but absent from B1 are reported, not deleted — a warehouse that
vanished from the read could equally be a filter mistake, and silently soft-deleting one
would strand any PO pointing at it. Pass --prune to deactivate them (never hard-delete).

Usage:
    python scripts/sync_b1_org_from_sap.py
    python scripts/sync_b1_org_from_sap.py --prune
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

# B1 serialises its Y/N flags as tYES / tNO.
_TRUE = {"tYES", "Y", "true", True}


def _b1_bool(v: object) -> bool:
    return v in _TRUE


def _mw_request(base: str, cookie: str, path: str, payload: dict | None, method: str) -> dict:
    req = urllib.request.Request(
        f"{base}{path}",
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Content-Type": "application/json", "Cookie": cookie},
        method=method,
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read() or b"{}")


def _mw_login(base: str, email: str, password: str) -> str:
    req = urllib.request.Request(
        f"{base}/auth/login",
        data=json.dumps({"email": email, "password": password}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.headers.get("set-cookie")
    if not raw:
        raise SystemExit("Login succeeded but no session cookie was returned.")
    return raw.split(";")[0]


def _b1_client():
    from app.sap_b1.client import get_b1_client

    return get_b1_client()


def _report(label: str, result: dict) -> None:
    print(f"\n  {label}")
    print(f"    created={result['created']}  updated={result['updated']}  skipped={result['skipped']}")
    for err in result.get("errors", [])[:8]:
        print(f"    skipped: {err}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://localhost:8001")
    ap.add_argument("--email", default="tech@letstryfoods.com")
    ap.add_argument("--password", default="TestPass123!")
    ap.add_argument("--prune", action="store_true",
                    help="Deactivate local rows B1 no longer returns (soft, reversible).")
    args = ap.parse_args()

    base = args.base_url.rstrip("/")
    client = _b1_client()

    print("Reading org structure from SAP B1 ...")
    places = client.query("BusinessPlaces")
    whs = client.query("Warehouses")
    print(f"  {len(places)} business place(s), {len(whs)} warehouse(s)")

    branches = [
        {
            "bpl_id": p["BPLID"],
            "bpl_name": p.get("BPLName") or f"Branch {p['BPLID']}",
            "disabled": _b1_bool(p.get("Disabled")),
            "street": p.get("Street"),
            "block": p.get("Block"),
            "city": p.get("City"),
            "zip_code": p.get("ZipCode"),
            "state": p.get("State"),
            "country": p.get("Country"),
            "gstin": p.get("GSTRegistrationNumber") or p.get("U_GSTIN"),
        }
        for p in places
    ]
    warehouses = [
        {
            "whs_code": w["WarehouseCode"],
            "whs_name": w.get("WarehouseName") or w["WarehouseCode"],
            "bpl_id": w["BusinessPlaceID"],
            "inactive": _b1_bool(w.get("Inactive")),
            "location": w.get("Location"),
            "street": w.get("Street"),
            "block": w.get("Block"),
            "city": w.get("City"),
            "zip_code": w.get("ZipCode"),
            "state": w.get("State"),
            "country": w.get("Country"),
        }
        for w in whs
        if w.get("BusinessPlaceID") is not None
    ]
    orphans = [w["WarehouseCode"] for w in whs if w.get("BusinessPlaceID") is None]
    if orphans:
        print(f"  ! {len(orphans)} warehouse(s) have no BusinessPlaceID and were left out: {orphans}")

    cookie = _mw_login(base, args.email, args.password)

    # Branches first — a warehouse naming an unknown branch is rejected by design.
    _report("POST /branches/sync",
            _mw_request(base, cookie, "/api/master-data/branches/sync", {"branches": branches}, "POST"))
    _report("POST /warehouses/sync",
            _mw_request(base, cookie, "/api/master-data/warehouses/sync", {"warehouses": warehouses}, "POST"))

    # Reconcile: what do we hold that B1 did not return?
    local = _mw_request(base, cookie, "/api/master-data/warehouses?limit=500", None, "GET")["items"]
    # Compare upper-cased: the sync endpoint normalises whs_code on receipt, so B1's
    # own "CONSU_Kl" is stored as "CONSU_KL" and a raw comparison reports it missing.
    b1_codes = {w["whs_code"].upper() for w in warehouses}
    stale = [w for w in local if w["whs_code"].upper() not in b1_codes]
    if not stale:
        print("\n  Reconciliation: every local warehouse exists in B1.")
    else:
        print(f"\n  Reconciliation: {len(stale)} local warehouse(s) not in B1:")
        for w in stale:
            print(f"    {w['whs_code']}  ({w['whs_name']})  active={w['is_active']}")
        if args.prune:
            for w in stale:
                _mw_request(base, cookie, f"/api/master-data/warehouses/{w['id']}",
                            {"is_active": False, "notes": "Not present in SAP B1 — deactivated by sync"},
                            "PUT")
            print(f"    → deactivated {len(stale)} (reversible; nothing deleted)")
        else:
            print("    → left alone. Re-run with --prune to deactivate them.")

    print("\nDone. Open Master Data → Warehouses.")


if __name__ == "__main__":
    sys.exit(main())
