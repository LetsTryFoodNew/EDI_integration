"""
Derive the SAP-facing Postman collection from the full one.

Shared outside the company, so it carries only what SAP calls: health, auth, the master
data they push, and the invoice endpoint. Everything else -- dashboards, the PO
lifecycle, the inboxes, ops-only PUT/edit routes -- is dropped, because handing a
partner a collection full of buttons they should not press invites exactly that.

Derived rather than hand-written so it cannot drift from the collection we test against.
"""
import json
from pathlib import Path

SRC = Path("docs/postman/edi-middleware.postman_collection.json")
OUT = Path("docs/postman/edi-middleware-sap.postman_collection.json")

#: folder prefix -> request names to keep. None means keep the whole folder.
KEEP: dict[str, set[str] | None] = {
    "00": None,   # Health
    "01": None,   # Auth — SAP needs a token
    "04": {       # Invoices
        "Push invoice from SAP",
        "Push invoice — line split across two batches",
        "NEGATIVE — batch quantities do not total the line (expect 422)",
        "List invoices",
        "Get invoice",
    },
    "05": {"Add or update customer  → 201 create / 200 update",
           "…send it again without integration config → 200, config survives",
           "Sync customers (from SAP)", "List customers  → sets {{partnerId}}"},
    "06": {"Add or update item  → 201 create / 200 update",
           "…send it again → 200, values updated",
           "Sync items (from SAP)", "List items  → sets {{materialId}}"},
    "07": None,   # SKU mapping
    "08": {"Sync ship-to (from SAP)", "List ship-to  → sets {{shipToId}}"},
    "09": {"Sync bill-to (from SAP)", "List bill-to  → sets {{billToId}}"},
    "10": {"Sync branches (from SAP)", "List branches  → sets {{branchId}}"},
    "11": {"Sync warehouses (from SAP)", "List warehouses  → sets {{warehouseId}}"},
}

src = json.loads(SRC.read_text())
folders = []
for f in src["item"]:
    prefix = f["name"].split(maxsplit=1)[0]
    if prefix not in KEEP:
        continue
    wanted = KEEP[prefix]
    kept = [i for i in f["item"] if wanted is None or i["name"] in wanted]
    if wanted is not None:
        missing = wanted - {i["name"] for i in f["item"]}
        if missing:
            raise SystemExit(f"{f['name']}: no such request(s): {sorted(missing)}")
    folders.append({**f, "item": kept})

out = {
    "info": {
        "name": "EDI Middleware — SAP B1 Integration",
        "description": (
            "Everything SAP Business One calls on the Let's Try Foods EDI middleware, "
            "and nothing else.\n\n"
            "START HERE\n"
            "1. Set the `baseUrl` and `email` / `password` collection variables.\n"
            "2. Run '01 · Auth → Login' — every other request reuses the token it saves.\n\n"
            "ONE ENDPOINT FOR ADD AND UPDATE\n"
            "Every push endpoint decides for itself whether a record is new. You never "
            "have to ask first and you never get a 409 for sending something twice.\n\n"
            "  materials           item_code                          -> ItemCode\n"
            "  partners            code                               -> CardCode\n"
            "  ship-to/sync        partner_code + buyer_whs_code      -> CardCode + Address\n"
            "  bill-to/sync        partner_code + buyer_bill_to_code  -> CardCode + Address\n"
            "  sku-mappings/sync   partner_code + buyer_sku           -> CardCode + item\n"
            "  invoices            b1_invoice_doc_entry, then invoice_number -> DocEntry\n\n"
            "Single-record endpoints answer 201 when they created and 200 when they "
            "updated. Batch (/sync) endpoints answer 200 and report "
            "created/updated/skipped counts — always read `errors[]`, a 200 does not "
            "mean every row was accepted.\n\n"
            "TWO KEYS WORTH NOTING\n"
            "SKU mappings key on `buyer_sku`, not `b1_item_code`: a customer can list "
            "one item under several of their own codes, and keying on the item would "
            "make the second push overwrite the first. `b1_item_code` is still required "
            "and must already exist in Item Master.\n\n"
            "Invoices match on `DocEntry` first because it is your immutable key; "
            "`invoice_number` is the fallback, since the first push usually has no "
            "DocEntry yet.\n\n"
            "WHAT AN UPDATE WILL NOT OVERWRITE\n"
            "Customer integration config (source_channel, gmail_label, webhook_secret, "
            "asn_sla_hours) — it describes how we fetch that retailer's orders, which a "
            "Business Partner record cannot express. Ops mappings (b1_whs_code, "
            "b1_bill_to_code, mapping_status). Soft-deleted rows, which answer 409 "
            "rather than being resurrected.\n\n"
            "Full contract: docs/sap-master-data-api.md and docs/sap-invoice-api.md."
        ),
        "schema": src["info"]["schema"],
    },
    "item": folders,
    "variable": src.get("variable", []),
}
if "event" in src:
    out["event"] = src["event"]
if "auth" in src:
    out["auth"] = src["auth"]

OUT.write_text(json.dumps(out, indent=2) + "\n")
total = sum(len(f["item"]) for f in folders)
print(f"wrote {OUT}  —  {len(folders)} folders, {total} requests")
for f in folders:
    print(f"  {f['name']}")
    for i in f["item"]:
        print(f"      {i['request']['method']:6} {i['name']}")
