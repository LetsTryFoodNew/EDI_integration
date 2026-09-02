"""
Load demo master data through the live sync API.

Unlike scripts/seed_master_data.py (which writes to the DB directly), this script
drives the four POST .../sync endpoints — the same path SAP will use. Running it
therefore verifies auth, all four sync handlers (create + update + skip branches),
and leaves the Master Data screens populated for manual inspection.

Usage:
    python scripts/load_demo_master_data.py
    python scripts/load_demo_master_data.py --base-url http://localhost:8000 \
        --email tech@letstryfoods.com --password 'TestPass123!'
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

# ── Customers (update-only: these 15 codes already exist) ─────────────────────
# b1_card_code is the one-time link to SAP; everything else is Customer master data.
PARTNERS = [
    dict(code="BLINKIT",        name="Blinkit (Grofers India Pvt Ltd)",       b1_card_code="C00012", gstin="27AAECG1234K1Z5", pan_card="AAECG1234K", business_type="Quick Commerce", group_name="Modern Trade",  phone_numbers=["+912240001200", "+919812345601"], email_address="vendor.ops@blinkit.com"),
    dict(code="ZEPTO",          name="Zepto (Kiranakart Technologies)",       b1_card_code="C00013", gstin="27AAFCD5862R1ZX", pan_card="AAFCD5862R", business_type="Quick Commerce", group_name="Modern Trade",  phone_numbers=["+912240001300"],                  email_address="supplier@zeptonow.com"),
    dict(code="SWIGGY",         name="Swiggy Instamart",                      b1_card_code="C00014", gstin="29AAGCB4576M1ZP", business_type="Quick Commerce", group_name="Modern Trade",  phone_numbers=["+918040001400"],                  email_address="instamart.vendors@swiggy.in"),
    dict(code="BIGBASKET",      name="BigBasket (Supermarket Grocery)",       b1_card_code="C00015", gstin="29AABCS1429B1Z0", business_type="E-Commerce",     group_name="Modern Trade",  phone_numbers=["+918040001500"],                  email_address="vendor@bigbasket.com"),
    dict(code="AMAZON",         name="Amazon Retail India Pvt Ltd",           b1_card_code="C00016", gstin="29AAICA4872D1ZK", business_type="E-Commerce",     group_name="Marketplace",   phone_numbers=["+918040001600"],                  email_address="vendorcentral@amazon.in"),
    dict(code="FLIPKART",       name="Flipkart Internet Pvt Ltd",             b1_card_code="C00017", gstin="29AACCF0683K1ZR", business_type="E-Commerce",     group_name="Marketplace",   phone_numbers=["+918040001700"],                  email_address="seller.support@flipkart.com"),
    dict(code="RELIANCE_JIO",   name="Reliance Retail / JioMart",             b1_card_code="C00019", gstin="27AABCR1718E1ZL", business_type="Hypermarket",    group_name="General Trade", phone_numbers=["+912240001900"],                  email_address="vendor.jiomart@ril.com"),
    dict(code="NATURES_BASKET", name="Nature's Basket (Godrej)",              b1_card_code="C00020", gstin="27AAACG1234M1Z2", business_type="Gourmet Retail", group_name="General Trade", phone_numbers=["+912240002000"],                  email_address="buying@naturesbasket.co.in"),
    dict(code="SPAR",           name="SPAR Hypermarket India",                b1_card_code="C00021", gstin="29AAECM7654N1ZQ", business_type="Hypermarket",    group_name="General Trade", phone_numbers=["+918040002100"],                  email_address="vendors@sparindia.com"),
    dict(code="METRO_CASH",     name="Metro Cash & Carry India",              b1_card_code="C00022", gstin="29AAACM2954A1ZW", business_type="Cash & Carry",   group_name="Wholesale",     phone_numbers=["+918040002200"],                  email_address="supplier.in@metro-cc.com"),
    dict(code="DUNZO",          name="Dunzo Daily",                           b1_card_code="C00023", gstin="29AAFCD8123L1ZN", business_type="Quick Commerce", group_name="Modern Trade",  phone_numbers=["+918040002300"],                  email_address="merchants@dunzo.in"),
    dict(code="ZOMATO_HP",      name="Zomato Hyperpure",                      b1_card_code="C00024", gstin="07AAECZ7891F1ZB", business_type="HoReCa Supply",  group_name="Wholesale",     phone_numbers=["+911140002400"],                  email_address="supply@hyperpure.com"),
    dict(code="BB_DAILY",       name="BB Daily (BigBasket Daily)",            b1_card_code="C00025", gstin="29AABCS1429B2Z9", business_type="Subscription",   group_name="Modern Trade",  phone_numbers=["+918040002500"],                  email_address="daily.vendor@bigbasket.com"),
    dict(code="MILKBASKET",     name="Milkbasket (Reliance)",                 b1_card_code="C00026", gstin="06AABCR1718E2ZH", business_type="Subscription",   group_name="Modern Trade",  phone_numbers=["+911240002600"],                  email_address="vendor@milkbasket.com"),
]

# ── Item master (create) ──────────────────────────────────────────────────────
# Deliberate spread: 5%/12%/18% tax, one frozen item and one invalid item so the
# UI's Frozen / Inactive states are both exercised.
def _item(code, name, hsn, tax, grp_cod, grp, case, lot, gram, ean, mrp, *, frozen=False, valid=True):
    vat = f"GST{int(tax):02d}"
    return dict(
        item_code=code, item_name=name, frgn_name=name, hsn=hsn, tax_rate=tax,
        itms_grp_cod=grp_cod, items_group_name=grp, invntry_uom="PCS", sal_unit_msr="CASE",
        vat_group_pu=vat, vat_group_sa=vat, case_size=case, lot_size=lot, grammage=gram,
        ean_code=ean, mrp=mrp, frozen_for=frozen,
        valid_for=1 if valid else 0, is_active=valid,
    )


ITEMS = [
    # Makhana — 12%
    _item("LTFM001", "Peri Peri Makhana 30g",        "20089900", 12, 103, "Makhana", 24, 24, "30g",  "8901234560001",  50.00),
    _item("LTFM002", "Classic Salted Makhana 30g",   "20089900", 12, 103, "Makhana", 24, 24, "30g",  "8901234560002",  50.00),
    _item("LTFM003", "Cheese Makhana 30g",           "20089900", 12, 103, "Makhana", 24, 24, "30g",  "8901234560003",  50.00),
    _item("LTFM004", "Himalayan Salt Makhana 80g",   "20089900", 12, 103, "Makhana", 12, 12, "80g",  "8901234560004", 120.00),
    _item("LTFM005", "Pudina Makhana 80g",           "20089900", 12, 103, "Makhana", 12, 12, "80g",  "8901234560005", 120.00),
    # Chips — 12%
    _item("LTFS001", "Spicy Potato Chips 50g",       "20052000", 12, 104, "Chips",   36, 36, "50g",  "8901234560010",  30.00),
    _item("LTFS002", "Baked Multigrain Chips 50g",   "20052000", 12, 104, "Chips",   36, 36, "50g",  "8901234560011",  35.00),
    _item("LTFS003", "Cream & Onion Chips 50g",      "20052000", 12, 104, "Chips",   36, 36, "50g",  "8901234560012",  30.00),
    _item("LTFS004", "Sea Salt Chips 100g",          "20052000", 12, 104, "Chips",   18, 18, "100g", "8901234560013",  55.00),
    # Nuts — 5%
    _item("LTFN001", "Roasted Mixed Nuts 100g",      "20081900",  5, 105, "Nuts",    12, 12, "100g", "8901234560020", 180.00),
    _item("LTFN002", "Salted Almonds 100g",          "20081900",  5, 105, "Nuts",    12, 12, "100g", "8901234560021", 220.00),
    _item("LTFN003", "Roasted Cashews 200g",         "20081900",  5, 105, "Nuts",    12, 12, "200g", "8901234560022", 380.00),
    # Trail mix / seeds — 5%
    _item("LTFT001", "Berry Trail Mix 150g",         "20081900",  5, 106, "Trail Mix", 12, 12, "150g", "8901234560030", 250.00),
    _item("LTFT002", "Pumpkin Seeds 200g",           "12079990",  5, 106, "Trail Mix", 12, 12, "200g", "8901234560031", 210.00),
    # Beverages — 18%
    _item("LTFB001", "Cold Brew Coffee 200ml",       "21011200", 18, 107, "Beverages", 24, 24, "200ml", "8901234560040",  99.00),
    _item("LTFB002", "Sparkling Lemon Water 330ml",  "22021010", 18, 107, "Beverages", 24, 24, "330ml", "8901234560041",  60.00),
    # Edge cases for the UI
    _item("LTFX001", "Seasonal Mango Bar 40g",       "20079990", 12, 108, "Seasonal",  48, 48, "40g",  "8901234560050",  25.00, frozen=True),
    _item("LTFX002", "Discontinued Trail Bar 40g",   "19053100", 18, 108, "Seasonal",  48, 48, "40g",  "8901234560051",  40.00, valid=False),
]

# ── SKU mappings, partner-wise ────────────────────────────────────────────────
# Every partner gets its own catalogue using that retailer's real SKU-code
# convention (Amazon ASINs, Flipkart FSNs, Blinkit EANs, ...). unit_price is derived
# from the item's MRP and the partner's negotiated margin, so PriceVarianceRule has
# realistic figures to compare an incoming PO against.
#
# Sync intentionally does NOT set material_id — that is an ops decision — so these
# land as UNMAPPED. The `map` flag marks the ones a second pass then maps through
# PUT /sku-mappings/{id}, i.e. the real ops path, so each partner ends up with a
# mix of mapped and unmapped rows.
_MRP = {i["item_code"]: i["mrp"] for i in ITEMS}

# partner_code -> (margin %, sku-code builder, [(buyer_sku_seed, item_code, map?)])
PARTNER_SKUS: dict[str, tuple[float, list[tuple[str, str, bool]]]] = {
    # Blinkit trades on EANs
    "BLINKIT": (35.0, [
        ("8901234560003", "LTFM003", True), ("8901234560004", "LTFM004", True),
        ("8901234560021", "LTFN002", False), ("8901234560040", "LTFB001", False),
        ("8901234560012", "LTFS003", True),
    ]),
    "ZEPTO": (34.0, [
        ("ZP-MM-003", "LTFM003", True), ("ZP-NT-001", "LTFN001", True),
        ("ZP-BV-001", "LTFB001", False), ("ZP-CS-004", "LTFS004", True),
        ("ZP-TM-001", "LTFT001", False),
    ]),
    "SWIGGY": (36.0, [
        ("SW-SNK-1001", "LTFM001", True), ("SW-SNK-1002", "LTFS001", True),
        ("SW-SNK-1003", "LTFS004", False), ("SW-SNK-1004", "LTFN001", False),
    ]),
    "BIGBASKET": (35.0, [
        ("BB-40001", "LTFN003", True), ("BB-40002", "LTFT001", True),
        ("BB-40003", "LTFT002", False), ("BB-40004", "LTFM005", False),
    ]),
    # Amazon uses ASINs
    "AMAZON": (32.0, [
        ("B0CJ4K7Q1M", "LTFM001", True), ("B0CJ4K8R2N", "LTFM004", True),
        ("B0CJ4K9S3P", "LTFN002", False), ("B0CJ4KAT4Q", "LTFT001", False),
    ]),
    # Flipkart uses FSNs
    "FLIPKART": (33.0, [
        ("SNKGZ7YHFHZQMK4T", "LTFS001", True), ("SNKGZ8ZJGIARNL5U", "LTFS002", False),
        ("NUTGZ9AKHJBSOM6V", "LTFN003", True),
    ]),
    "RELIANCE_JIO": (37.0, [
        ("491203001", "LTFM001", True), ("491203002", "LTFS004", False),
        ("491203003", "LTFN001", False),
    ]),
    "NATURES_BASKET": (34.0, [
        ("NB-70011", "LTFT001", True), ("NB-70012", "LTFN003", False),
        ("NB-70013", "LTFB002", False),
    ]),
    "SPAR": (36.0, [
        ("SPAR-5501", "LTFM002", True), ("SPAR-5502", "LTFS002", False),
    ]),
    "METRO_CASH": (30.0, [
        ("MET-880011", "LTFM004", True), ("MET-880012", "LTFN002", True),
        ("MET-880013", "LTFT002", False),
    ]),
    "DUNZO": (35.0, [
        ("DZ-3301", "LTFM001", True), ("DZ-3302", "LTFB001", False),
    ]),
    "ZOMATO_HP": (28.0, [
        ("HP-990101", "LTFN003", True), ("HP-990102", "LTFT002", True),
        ("HP-990103", "LTFM005", False),
    ]),
    "BB_DAILY": (35.0, [
        ("BBD-2201", "LTFB002", True), ("BBD-2202", "LTFM003", False),
    ]),
    "MILKBASKET": (34.0, [
        ("MB-6601", "LTFB001", True), ("MB-6602", "LTFM002", False),
    ]),
}


def _build_sku_payload() -> list[dict]:
    """Build the SKU_Mapping sync payload. b1_item_code is required on every row."""
    payload: list[dict] = []
    names = {i["item_code"]: i["item_name"] for i in ITEMS}
    for partner_code, (margin, rows) in PARTNER_SKUS.items():
        for buyer_sku, item_code, _ in rows:
            price = round(_MRP[item_code] * (1 - margin / 100), 2)
            payload.append(dict(
                partner_code=partner_code, buyer_sku=buyer_sku,
                b1_item_code=item_code, item_name=names[item_code],
                unit_price=price, margin=margin, qty_per_buyer_uom=1,
            ))
    # Two deliberate rejects: unknown partner, and an item code absent from Item_master.
    payload.append(dict(partner_code="NOT_A_PARTNER", buyer_sku="XX-0001",
                        b1_item_code="LTFM001", item_name="Unknown partner", unit_price=1.0, margin=1.0))
    payload.append(dict(partner_code="BLINKIT", buyer_sku="XX-0002",
                        b1_item_code="NO_SUCH_ITEM", item_name="Unknown item", unit_price=1.0, margin=1.0))
    return payload

# ── Ship-to addresses (create) ────────────────────────────────────────────────
# Seller is in Maharashtra, so MH rows are intra-state (CGST+SGST) and everything
# else is inter-state (IGST) — both paths represented.
SHIP_TO = [
    dict(partner_code="BLINKIT", buyer_whs_code="BL-BLR-001", buyer_warehouse_name="Blinkit Bengaluru DC",
         street="Survey 88, Soukya Road", block="Whitefield", city="Bengaluru", zip_code="560067",
         state="Karnataka", gst_registration_no="29AAECG1234K1ZT",
         poc_name="Rakesh Sharma", poc_email="rakesh.s@blinkit.com", poc_phone="+919812345601"),
    dict(partner_code="BLINKIT", buyer_whs_code="BL-HYD-001", buyer_warehouse_name="Blinkit Hyderabad DC",
         street="Plot 32, Medchal Industrial Area", block="Medchal", city="Hyderabad", zip_code="501401",
         state="Telangana", gst_registration_no="36AAECG1234K1ZF"),
    dict(partner_code="ZEPTO",   buyer_whs_code="ZP-DEL-001", buyer_warehouse_name="Zepto Delhi Dark Store",
         street="Khasra 210, Bijwasan", block="South West Delhi", city="New Delhi", zip_code="110061",
         state="Delhi", gst_registration_no="07AAFCD5862R1ZC"),
    dict(partner_code="ZEPTO",   buyer_whs_code="ZP-PUN-001", buyer_warehouse_name="Zepto Pune Dark Store",
         street="Gat 122, Chakan MIDC", block="Chakan", city="Pune", zip_code="410501",
         state="Maharashtra", gst_registration_no="27AAFCD5862R1ZX"),
    dict(partner_code="SWIGGY",  buyer_whs_code="SW-BLR-001", buyer_warehouse_name="Swiggy Bengaluru Hub",
         street="Sy 17, Bommasandra", block="Anekal", city="Bengaluru", zip_code="560099",
         state="Karnataka", gst_registration_no="29AAGCB4576M1ZP"),
    dict(partner_code="BIGBASKET", buyer_whs_code="BB-CHN-001", buyer_warehouse_name="BigBasket Chennai DC",
         street="Plot 5, Oragadam Industrial Corridor", block="Sriperumbudur", city="Chennai", zip_code="602105",
         state="Tamil Nadu", gst_registration_no="33AABCS1429B1ZV"),
    dict(partner_code="BIGBASKET", buyer_whs_code="BB-KOL-001", buyer_warehouse_name="BigBasket Kolkata DC",
         street="NH6, Dhulagarh Logistics Park", block="Sankrail", city="Howrah", zip_code="711302",
         state="West Bengal", gst_registration_no="19AABCS1429B1ZD"),
    dict(partner_code="AMAZON",  buyer_whs_code="AMZ-BOM7",   buyer_warehouse_name="Amazon BOM7 Fulfilment Centre",
         street="Village Vahuli, Padgha", block="Bhiwandi", city="Thane", zip_code="421101",
         state="Maharashtra", gst_registration_no="27AAICA4872D1ZS"),
    # Unknown partner — must be SKIPPED and reported
    dict(partner_code="NOT_A_PARTNER", buyer_whs_code="XX-DC-1", buyer_warehouse_name="Should be skipped",
         city="Nowhere", state="Nowhere"),
]


def _request(base_url: str, cookie: str, path: str, payload: dict | None, method: str) -> dict:
    req = urllib.request.Request(
        f"{base_url}{path}",
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Content-Type": "application/json", "Cookie": cookie},
        method=method,
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read())


def _post(base_url: str, cookie: str, path: str, payload: dict) -> dict:
    return _request(base_url, cookie, path, payload, "POST")


def _login(base_url: str, email: str, password: str) -> str:
    req = urllib.request.Request(
        f"{base_url}/auth/login",
        data=json.dumps({"email": email, "password": password}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw_cookie = resp.headers.get("set-cookie")
    if not raw_cookie:
        raise SystemExit("Login succeeded but no session cookie was returned.")
    return raw_cookie.split(";")[0]


def _report(label: str, result: dict) -> None:
    print(f"\n  {label}")
    print(f"    created={result['created']}  updated={result['updated']}  skipped={result['skipped']}")
    for err in result.get("errors", [])[:5]:
        print(f"    skipped: {err}")
    extra = len(result.get("errors", [])) - 5
    if extra > 0:
        print(f"    ... and {extra} more")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://localhost:8000")
    ap.add_argument("--email", default="tech@letstryfoods.com")
    ap.add_argument("--password", default="TestPass123!")
    args = ap.parse_args()

    base = args.base_url.rstrip("/")
    print(f"Loading demo master data via {base} ...")

    try:
        cookie = _login(base, args.email, args.password)
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"Login failed ({exc.code}). Check --email/--password.") from exc

    sku_payload = _build_sku_payload()

    _report("POST /partners/sync",     _post(base, cookie, "/api/master-data/partners/sync",     {"partners": PARTNERS}))
    _report("POST /materials/sync",    _post(base, cookie, "/api/master-data/materials/sync",    {"items": ITEMS}))
    _report("POST /sku-mappings/sync", _post(base, cookie, "/api/master-data/sku-mappings/sync", {"mappings": sku_payload}))
    _report("POST /ship-to/sync",      _post(base, cookie, "/api/master-data/ship-to/sync",      {"mappings": SHIP_TO}))

    print("\nDone. Open the Master Data screen to inspect.")


if __name__ == "__main__":
    sys.exit(main())
