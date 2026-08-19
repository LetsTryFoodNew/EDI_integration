# Backlog

Ideas deliberately **not** built, recorded so they are not silently forgotten or
silently smuggled into an unrelated change (CLAUDE.md §9.11).

---

## Validate `ship_to_mapping.b1_whs_code` against Warehouse Master

**Raised:** 2026-08-19, while adding `branch_master` / `warehouse_master`.

`ship_to_mapping.b1_whs_code` is the B1 warehouse an ops user assigns to a retailer's
DC. It is a free-text `String(20)` with nothing checking it. Until now there was nothing
to check it *against* — the middleware had no list of our warehouses. Now it does.

A typo there is invisible until the Sales Order push, where B1 returns a generic
"invalid warehouse" and the PO lands in `SAP_REJECTED` with no hint which field is
wrong. Two cheap improvements, in order of appetite:

1. **Warn on `PUT /api/master-data/ship-to/{id}`** when `b1_whs_code` matches no active
   row in `warehouse_master` — a `warnings[]` entry on the response, like
   `TradingPartnerWriteResponse` already does. Non-blocking, so a warehouse not yet
   synced does not stop ops working.
2. **Check it in `po_to_sales_order`** before the push, turning a B1 round-trip failure
   into a local validation issue with the offending code named.

Deliberately *not* a database FK: `b1_whs_code` predates `warehouse_master` and existing
rows would fail the constraint on migration, and a hard FK would also block ops from
pre-assigning a warehouse that SAP has not pushed yet.

Also worth pairing with it: surface `bpl_id` on the Sales Order payload from the mapped
warehouse's branch, rather than relying on B1 to infer the branch. B1 rejects documents
whose warehouse and branch disagree, and we now hold both.

