# Changelog

## One endpoint for add and update, keyed on what SAP calls the record (2026-09-05)

SAP asked for a single API per resource that both adds and updates. Two of the six
were create-only and answered `409` on a repeat, which made SAP ask first — POST, read
the conflict, switch to PUT — two round trips, a race between them, and a `409` that
said nothing actionable.

    POST /api/master-data/materials    item_code   -> ItemCode   201 create / 200 update
    POST /api/master-data/partners     code        -> CardCode   201 create / 200 update
    POST /api/master-data/ship-to/sync partner_code + buyer_whs_code       already upserted
    POST /api/master-data/bill-to/sync partner_code + buyer_bill_to_code   already upserted
    POST /api/master-data/sku-mappings/sync  partner_code + buyer_sku      already upserted
    POST /api/invoices                 b1_invoice_doc_entry, then invoice_number

**Invoices now match on `DocEntry` first.** It is SAP's immutable key, so a re-push
carrying it lands on the same row whatever else changed. `invoice_number` stays the
fallback and stays unique: the first push of an invoice usually has no DocEntry yet —
B1 assigns it on posting and the IRN arrives later still — and the number is what the
retailer reconciles against. A DocEntry arriving under a *different* invoice_number is
**refused**, not applied: B1 does not renumber a posted invoice, so it means a
cancel-and-repost or a wrong DocEntry, and renaming a stored invoice would break the
ASN the retailer already holds against the old number.

**What an update will not overwrite.** A Business Partner record has nothing to say
about how we fetch that retailer's orders, so `source_channel`, `gmail_label`,
`webhook_secret` and `asn_sla_hours` are written on create and thereafter only where
still empty. `TradingPartnerCreate` defaults `source_channel` to MANUAL, which makes
"MANUAL" on an update indistinguishable from "not supplied" — applying it would demote
a live API partner and stop its polling with no error anywhere. Soft-deleted rows
answer `409` rather than being resurrected: `deleted_at` was set by a person.

**Two keys differ from the table SAP sent, deliberately.** SKU mappings key on
`buyer_sku`, not `b1_item_code` — a customer can list one item under several of their
own codes, and keying on the item would make the second push overwrite the first;
`sku_mapping` is unique on `(customer, buyer_sku)` in the schema. And invoices carry
the `invoice_number` fallback described above rather than DocEntry alone.

`docs/sap-master-data-api.md` §4a and `docs/sap-invoice-api.md` §6 document the
contract for whoever writes the SAP side.

## DMart removed as a trading partner (2026-08-25)

Removed at the user's request — DMart never had a working parser (no `DmartEmailAdapter`/
parser ever existed) or ship-to/SKU mapping data; every one of its 254 POs on both local
and production was an `E000_PARSE_FAILED` placeholder from failed parse attempts, with
zero line items, invoices, ASNs, or outbound messages ever created for it.

- Deleted the `DMART` row from `trading_partners` and every dependent row (254 POs, 254
  raw_messages, 254 validation_issues, 2 status_history entries, 1 ship_to_mapping) on
  **both** the local dev DB and the production server, via `scripts/remove_partner.sql`
  (children-before-parents, single transaction, identical script run on both — verified
  matching row counts on each side before commit).
- Removed DMart from `scripts/load_demo_master_data.py` (partner record, tax-rate/SKU
  seed group, ship-to warehouse mapping) and the two doc-comment mentions in
  `app/workflows/ingest_to_canonical.py` and `app/adapters/outbound/email_outbound.py`.
- Updated `CLAUDE.md` §1 and `PLAYBOOK.md`'s Gmail label table to drop DMart from the
  platform list, so it isn't reintroduced by a future session reading the spec.
- Left the DMart mention in this file's 2026-08-XX `source_channel` entry untouched —
  changelog entries are a historical record of what was true when written, not live docs.
- App verified healthy on both databases after removal.

## An invoice line can now name the batches it was filled from (2026-08-26)

`batch_number` took one string per line, so a line picked from two batches could only
name one of them. The retailer books stock against a batch and matches it at goods-in,
so the split is the part they need.

    "batch_number": "LTF-202608-A"

    "batch_number": [{"batchNumber": "LTF-202608-A", "quantity": 234},
                     {"batchNumber": "LTF-202608-B", "quantity": 66}]

The string form still works and means the list form with one entry for the whole line
quantity, so nothing already sending it has to change. `batchNumber`/`batch_number` and
`expiryDate`/`expiry_date` are both accepted — the camelCase spellings are what the
partner contracts use. A batch without its own expiry inherits the line's.

**Batch quantities must total the line's `qty`,** and the invoice is rejected
otherwise. Summing to less silently under-declares the shipment, summing to more
declares stock that was never invoiced; either way the retailer's goods-in disagrees
with the invoice and it is found at the dock.

**Multi-batch goes out as multiple rows, because neither partner can express it any
other way.** Blinkit §12.3 types `batch_number` as a single string per item and Zepto's
`batchDetails` is one object per `itemDetails` entry. A split line ships as several
rows sharing every per-unit figure, with only the quantity divided.

Three things that had to be right for that split to be honest:

- `unit_landing_price` is `line_total / qty`, so it takes the **line** quantity. Given
  a batch quantity, a 66-of-300 batch would have looked 4.5× more expensive per unit.
- Blinkit's `item_count` is "Number of unique" (§7), not a row count — it now counts
  distinct `item_id`, so a one-SKU shipment does not report two.
- Zepto's `itemSequenceNumber` numbers the rows sent and stays unique, while
  `articleSequenceNumber` identifies the article and repeats across its batches.

**This fixed a latent bug.** Both builders keyed ASN rows as `{item_code: line}`, which
kept only the last row for any item. Nothing produced multiple rows per item before, so
it never fired — but the moment one did, a batch would have vanished from the ASN while
its quantity stayed in the invoice total, telling the retailer less had shipped than
was billed.

## Every edit of a keyed-in order failed to parse (2026-08-26)

Saving a change to a line came back "parse failed" every time, while the original
entry had gone through. The form was fine; nothing had been entered wrongly.

The workers were running an image built before `manual_parser.py` existed. Backend
services in `docker-compose.yml` bind-mount only `./credentials`, so the code they run
comes entirely from the baked image — and the manual parser had been reaching the
running system by hot-patch into the `api` container alone. `api` parses nothing:
`POST /entries` enqueues to the `ingest` queue, and `worker-ingest` had neither the
parser nor the payload routing that reaches it. Every entry fell through to the
partner registry, which has no parser for LOTS.

The reason edits looked worse than first entries is an artefact of how it was tested,
not of the code: a first entry that had also been parsed in-process inside `api`
recorded SUCCESS, so only the revision showed the failure. Through the UI both fail
equally.

Rebuilt and restarted. Verified through the real path — POST, worker parses, no
in-process shortcut:

    submit    201 queued=True   worker parse_status=SUCCESS
    save edit 201 rev=2         worker parse_status=SUCCESS
      v1 SUPERSEDED  qty=36  item=FG00319  price=31.43  total=1188.05
      v2 VALIDATED   qty=72  item=FG00325  price=29.33  total=2217.35

**The message it failed with was the real cost.** "No parser registered for partner
'LOTS'" sends whoever reads it hunting for a partner parser that was never supposed to
exist — a hand-keyed order needs none. A raw message carrying `_entry_type` that
reaches the fallback now says what is actually wrong: the running code is missing the
manual parser, rebuild and restart the workers. No code guard can protect against a
worker running yesterday's image, but the error it produces can at least point at it.

## Picking an item fills the line from that partner's own master data (2026-08-26)

The item picker searched the material master, so choosing an item filled the
description and left the operator to type the buyer SKU, the UoM and — the one that
matters — the unit price, all of which are already recorded against that partner.

`GET /api/manual-inbox/catalogue` returns what a partner actually buys: their SKU
mappings first, carrying the things only a mapping knows (their buyer SKU, the
contracted unit price, the UoM they order in), then items the master knows but this
partner has never been sold. Both in one list, because a hand-keyed order is often for
something new to them and hiding it would send the operator to Master Data mid-entry.
Unmapped rows are labelled as such, so a blank price afterwards reads as expected
rather than as a bug.

Where the two sources overlap the mapping wins — a contracted price for LOTS is not
the price for anyone else. Item data fills the rest, since HSN, MRP, EAN and case size
are properties of the product whoever is buying it.

**Quantity is the one field never filled.** Nothing in master data knows how many were
ordered; it is the only number genuinely on the paper in front of the operator.

Blank fields are filled and typed ones kept — picking an item asks for its defaults,
not for your work to be discarded. Re-picking a *different* item on a line that
already had one does overwrite, because that is someone changing their mind and the
previous item's price must not be left behind.

The picker also searches by buyer SKU, so `104584368` and `FG00319` both find the same
row and the operator can work from whichever number the order quotes.

**Not filled today: the GST rate.** `material_master.tax_rate` and `vat_group_sa` are
empty for all 182 items — the item sync does not bring tax data across. A rate we do
not know is left alone rather than guessed, and the form's 5% default stands.

## A keyed-in order can be corrected after it is submitted (2026-08-26)

We author manual orders, so a typo in one is ours to fix — unlike a partner's PO,
where our copy has to keep matching the one they hold. The Manual Inbox now has an
Edit action on every parsed order, prefilled with what was originally typed.

**A correction is a new revision, not an in-place edit.** `raw_messages` is the
immutable record of what arrived and the existing versioning supersedes the previous
version, so the 500 someone meant to type as 50 stays visible next to the fix rather
than being quietly overwritten.

**Editing stops when the order leaves the building.** Up to the SAP push it exists
only here and a correction costs nothing. Once there is a Sales Order, an invoice or
an ASN against it, someone else is already acting on that document and a quiet edit
would leave the two disagreeing with no trace of which is right. Checked on the read
*and* on the write, because a form can have been open since before the push.

**Lines can name their SAP item directly.** This is what made the feature usable at
all: a manual partner has no catalogue for a buyer SKU to be mapped from, so every
LOTS line came back `E002_SKU_UNRESOLVED` and no hand-keyed order could ever be
pushed. The operator picks from the material master through a combobox — never free
text, because an item code typed from memory is how Blinkit PO 2873410040494 reached
SAP naming FG00460 and B1 rejected all eighteen lines with ODBC -2028.

`SkuMappingRule` accepts a line that already names an item, and this is deliberately
*not* a hole in it. What the rule refuses to do is guess; no partner parser sets
`sap_material_no`, so the only way one is present before validation is that a person
chose it. The code is still checked against master data, so a stale or mistyped one is
rejected rather than posted.

Found while testing: `_save_canonical_po` dropped `sap_material_no` on the way to the
database, so the operator's explicit choice was discarded and the PO came back
unmapped anyway.

**Worth knowing:** `FG00460` is present in our local `material_master` but absent from
SAP. That drift is why the Blinkit PO reached the Service Layer at all — validation
checks our copy, and our copy said the item was fine. A re-sync would clear it.

## The partner's own PO now travels with the ASN and the invoice (2026-08-26)

An ASN carrying only a tax invoice still leaves the retailer's accounts desk to go and
find the order it settles. All three documents now go in one email.

The source PO is read back from wherever ingestion stored it rather than re-rendered:
the point is to hand them the same document they issued, and a reconstruction of it
would invite an argument about which copy is authoritative. Partners on an API have no
such file and manually keyed orders have none either — both simply contribute nothing.

The Cloudinary signing dance that fetches those files lived inside the inbox route.
Two callers need it now, so it moved to `app/adapters/storage.fetch_attachment` rather
than being copied — a second implementation of a signed private-download URL would
drift from the first.

Two things worth naming, both found by reading the delivered mail rather than trusting
the send:

- **Filenames.** Swiggy's attachment is called
  `DG8TMD12QLBDILJRUSF7_CREATE_OTB_PURCHASE_ORDER_ae4e21a5-814d-4c24-9bb2-...xlsx`,
  which identifies nothing and is what an accounts desk has to find again later. They
  are renamed to `PO-CMMPO17234.pdf` / `.xlsx`, with a numeric suffix when a partner
  sends two of the same type so the second does not silently replace the first in
  someone's downloads folder.
- **Content types.** `mimetypes.guess_type` is backed by the system mime database and
  the slim image has no entry for `.xlsx`, so the spreadsheet went out as
  `application/octet-stream` — which mail clients will not preview. The formats
  partners actually send are now mapped explicitly.

Failures degrade rather than cascade: an unreadable source file still sends the
invoice, a failed invoice render still sends the PO, and an oversized set is trimmed
under Gmail's 25 MB ceiling, because a delivery note carrying one document beats one
that never arrives.

## The tax invoice now rides along with the ASN email (2026-08-26)

`app/utils/invoice_pdf.py` has said in its own docstring since it was written that it
serves "the ops Download PDF action **and** the attachment on outbound email to
mail-based partners". Only the first half was ever wired up, so Swiggy got a delivery
note with no invoice behind it and no way to reconcile the shipment.

**Named, not carried.** The envelope records `attach_invoice: "<invoice_number>"` and
`send_outbound._with_attachments` renders the PDF from the invoice record immediately
before dispatch. A stored copy would be tens of kilobytes of base64 in a JSONB column
per ASN, would make the Outbound Messages tab unreadable, and would go stale the
moment an IRN arrived on a re-push — regenerating always matches the record. Rendering
in the workflow rather than the adapter also keeps `BaseOutboundAdapter`'s rule intact:
the adapter transports and never touches the DB.

A render failure logs and sends without the attachment rather than raising. The
covering email is still worth delivering.

**MIME structure matters here.** text and html are the *same* content and the reader
picks one, so they stay inside `multipart/alternative`; a PDF is different content and
belongs beside them, so the presence of an attachment wraps the whole message in
`multipart/mixed`. Attaching a file directly into `alternative` makes mail clients
treat it as another rendering of the body and quietly hide it.

Fixed while testing: an ASN whose invoice number was missing everywhere crashed the
HTML body — `html.escape(None)` raises. Numbers are coerced at the source and empty
header rows are dropped rather than printed with nothing after the colon.

Verified on Swiggy PO CMMPO17234 by reading the sent message back out of Gmail:
`multipart/mixed` → `multipart/alternative` (text 1,320 B + html 7,524 B) plus
`Invoice-DUMMY-SWIGGY-46581.pdf`, 5,278 bytes, downloaded and confirmed a real PDF
(`%PDF-1.4` … `%%EOF`) carrying all 14 lines, both GSTINs and the IGST column.

## No 855 acknowledgement has ever had a recipient (2026-08-26)

With the Gmail scope fixed and the ASN for CMMPO17234 delivered, its 855 still
refused to send: Gmail answered 400 "Recipient address required".

`_partner_email` read only `partner.api_config["ops_email"]` and ignored
`trading_partners.email_address` — the first-class column, the one Master Data edits,
and the one holding `instamart.vendors@swiggy.in`. Every 855 and every email 856 built
through `b1_to_outbound` was therefore addressed to `""`. Swiggy's ACK had been
retrying since creation for that reason and nothing else.

It failed loudly only because Gmail rejects an empty To header. An adapter less strict
would have reported the acknowledgement delivered, and an SLA breach on a document
nobody received is invisible until the retailer asks where it is.

Now `email_address` first, `ops_email` as a fallback so partners configured the old way
keep working, and a blank column falls through rather than winning.

## Swiggy's ASN email had no recipient and no subject (2026-08-26)

Pushing Swiggy PO CMMPO17234 exposed the mail path, which had never actually
delivered anything.

`BaseOutboundAdapter` is explicit that payload construction happens before dispatch
and the adapter must not read the DB, so `EmailOutboundAdapter._build_mime` reads
`to`, `subject` and `body_text` straight off the stored payload. Nothing ever put
them there: `_partner_asn_payload` had wire-format builders for Blinkit and Zepto and
handed everyone else the partner-neutral shipment body. That body has none of those
three keys, so the Swiggy ASN was headed for Gmail with an empty To header and a
subject of "(no subject)" — delivered nowhere, and recorded as SENT.

`app/adapters/outbound/email_asn.py` renders the envelope at message creation time,
which also means the exact email is visible in the Outbound Messages tab before it is
sent rather than materialising inside Gmail. Both a plain-text and an HTML part:
warehouse mailboxes and EDI mailbots routinely strip HTML, and the shipment lines
have to survive that. Quantities lose their stored scale (15.0000 reads as a mistake
on a delivery note), absent optional fields are omitted rather than printed as
"None", and the canonical body is kept under `asn` so nothing is lost. A partner with
no `email_address` warns instead of addressing mail to nobody.

**Gmail scope.** The token was minted `gmail.readonly`, so the send answers 403
"insufficient authentication scopes" at dispatch, after the ASN is queued. The
adapter already asked for `gmail.send` when loading credentials, but granted scopes
are baked into the refresh token — asking at load time does nothing. `SCOPES` now
carries both in `gmail_client.py` and `auth_gmail.py`; **`scripts/auth_gmail.py` has
to be re-run** to mint a token that can send, which needs a browser consent.

Also on CMMPO17234: partner CardCode C00014 -> D00002 (SAP holds Swiggy's buying
entity as SCOOTSY LOGISTICS PVT LTD (MAIN), the name printed on the PO), and the
blank buyer GSTIN that raised E001 filled with 36AAVCS1691R2Z5. That GSTIN was not
guessed — the PO ships to "143E/A1 ... Mangalapally Village, Ibrahimpatnam,
Rangareddy, Telangana-501510", and D00002 carries exactly that address as
"501510  143E/A1-SCOOTSY". Its name is set as ship-to and bill-to so B1 books against
that DC: D00002 has 109 addresses across twenty-odd states, and without naming one B1
picks the customer default and the place of supply is wrong.

## Manual Inbox can now take a purchase order (2026-08-26)

LOTS Wholesale send orders by phone and paper; Reliance/JioMart publish theirs on a
portal whose scraper is Phase 9 work. Both partners sat in the Manual Inbox showing
"No documents yet — coming in the next phase". They can now be keyed in.

A keyed-in order is stored as a raw_message exactly like a webhook body and handed to
the same pipeline: parse, validate, SKU mapping, SAP push, 855/856 outbound. Nothing
downstream can tell it from a Blinkit webhook, which is the point — manual partners
get the whole chain without a line of partner-specific code.

**What the operator types, and what is derived.** They type quantity, unit price and
one GST rate per line. Taxable amounts, the CGST/SGST-vs-IGST split, line totals and
header totals are computed in `ManualEntryParser`. A hand-typed total that disagrees
with its own lines by a rupee trips `TotalReconciliationRule` and parks the order in
the exceptions queue, so deriving means the document reconciles by construction and
there are fewer boxes to get wrong.

**The split follows place of supply**, per `app/utils/gst.py` and CLAUDE.md §8: seller
state against ship-to state, GSTIN prefix preferred over a typed state name because
the prefix is unambiguous. When either state is unknown this refuses rather than
guessing — silently defaulting to intra-state would put CGST+SGST on an inter-state
order and the error would surface at the retailer's reconciliation. The form and the
route both check it, so the message lands on the field rather than as a parse failure
minutes later.

CGST and SGST are each computed from the halved rate rather than by halving the
combined amount. On a 167.15 total, halving gives 83.58 and 83.57, and a CGST that
differs from its SGST is queried at filing. Equal halves can sum to a paisa under the
combined figure, which is safe here only because every total downstream is a sum of
these line amounts — nothing computes tax a second way to disagree with.

- `app/parsers/manual_parser.py` — `ManualEntryParser`, routed on the payload marker
  rather than the partner code, so one parser covers every manual partner and a new
  one needs no code. A partner that also has a wire still goes to its own parser.
- `POST /api/manual-inbox/entries` — refuses a partner that receives orders over a
  wire (keying one in would double an order that also arrives on its own), and
  refuses a repeated PO number, which on hand-keyed input is far more likely to be a
  double submission than a revision. `replace_existing` files it as one, and the
  existing versioning supersedes the previous.
- The operator's keystrokes are stored verbatim; the seller's GSTIN and state are
  copied in at entry rather than read at parse time, so editing the seller entity
  later cannot silently re-tax an old order.
- New PO form on the Manual Inbox page: header, ship-to, an add/remove line table and
  a live total for checking against the paper before submitting. The preview is
  labelled indicative — the saved figures come from the server.

## Zepto's expiry notices were being filed as purchase orders (2026-08-25)

A poll took the Zepto PO count from 475 to 503 on a day Zepto had raised four POs.
The suspicion was the 15-day minimum lookback, but that window is fine: it only
governs how much history we *ask* for, and `raw_messages` is unique on Zepto's
`eventId`, so re-requesting the overlap costs nothing.

What the wider window actually surfaced was Zepto's housekeeping. When a PO passes
its `expiryDate` Zepto emits an `UpdatePO` carrying `status: EXPIRED`, stamped
18:30:00Z — midnight IST, a nightly sweep. Those events arrive in the same feed as
real POs, and reach back to orders raised long before we started polling: 210 of
them landed stamped 16 Aug alone. Two defects between the parser and the database
turned each one into work for ops.

`_save_canonical_po` hardcoded `po_status=PARSED`. `ZeptoParser` had already worked
out that an EXPIRED PO is CANCELLED, and `validate_po` has refused to touch a
CANCELLED PO since 2026-07-28 — but the persistence layer sitting between them threw
the verdict away, so the guard never fired once. 533 dead Zepto POs were validated
anyway and queued as exceptions asking ops to map SKUs for goods nobody would ship.

Worse, an expiry for a PO number we had never held created a **new** purchase order
at version 1, because `_find_existing_po` found nothing to supersede. 303 of the 540
Zepto POs on the server existed for no other reason: born already dead, from notices
about orders we never received. That is what made four real POs read as twenty-eight.

- `_persisted_status()` honours a parser's terminal status and ignores anything
  else, since `EDI850.po_status` defaults to RECEIVED and most parsers never set it
  — a non-terminal value is silence, not an opinion.
- `_is_orphan_terminal_notice()` skips a terminal document for a PO number we have
  never held: `parse_status = "SKIPPED"`, no PO row. An expiry for a PO we *do* hold
  still processes normally — closing out a live order is the point of the notice.
- Validation is no longer enqueued for a PO that arrives already terminal.
- The inbox shows SKIPPED as "Not applicable" rather than falling through to
  "Pending", which implied a backlog that was never going to clear.

`scripts/purge_orphan_expiry_pos.py` retires what was already stored, deciding
terminal-ness by re-running the partner's own parser rather than hardcoding any
partner's status vocabulary. On the server: 301 orphans soft-deleted, 2 kept because
they carry invoices. Zepto is down from 545 rows to 283, and no other partner was
affected.

What remains is real: 104 CreatePO/RELEASED events arrived today for vendor KK-1102,
all stamped between 08:13:12Z and 08:13:14Z. Zepto's QA environment generates POs in
bulk; that is their data, not our double-counting.

## Blinkit rejected our ASN because Go will not take 360.0 as an int (2026-08-22)

First real contact with Blinkit's API from the whitelisted server, and it refused
the ASN with HTTP 400:

```
invalid request body: failed to decode request body:
json: cannot unmarshal number 360.0 into Go struct field
Item.items.quantity of type int
```

Their API is Go. `encoding/json` accepts the literal `360` into either an `int` or
a `float64` field, but refuses `360.0` for an `int` one. We were serialising every
numeric through `float()`, so an ordered quantity of 360 went out as `360.0` and
their decoder stopped at the first item.

The contract calls the field a "number" (§12.7) without saying which, so the fix is
to send the encoding both field types accept: `_num()` emits an int when the value
is integral and a float otherwise. Thirteen fields changed shape on the stored
payload — `quantity`, `mrp`, `uom.value` and `total_additional_cess_value` across
all three lines. Genuinely fractional values are untouched: `gst_percentage` 2.5 and
`gst_total` 1087.71 still go out as floats, since an `int` field would reject those
whatever we did.

`quantity` gets `_qty()` rather than `_num()`, because Blinkit types it as an
integer outright. A fractional quantity is rounded **and warned about** instead of
silently truncated — a shipment notice that understates what is on the truck is
worse than one that fails to send.

### Why the error was readable at all

The previous entry's `_parse_blinkit_error` fix landed first. Before it, this same
rejection recorded as `"Validation failed"` and nothing more, three times over.
The Go decoder message names the exact field and value; we had been discarding it.

### Stored payloads had to be rebuilt too

The ASN body is built when the invoice arrives and parked in
`edi_outbound_messages.payload`, so what will be sent is visible before dispatch.
That also means a builder fix does not reach an ASN that is already queued — the
retry re-sends the same bytes and earns the same rejection.
`scripts/rebuild_asn_payloads.py` re-runs the builder over unsent BLINKIT messages,
prints which fields changed type, and leaves `next_retry_at` alone so a held
message stays held.

## Infra — the outbound queue had no worker (2026-08-20)

Nothing the middleware produced for a retailer had ever been sent. Not one 855 ACK,
not one 856 ASN. The queue names the code enqueued to and the queue names the workers
consumed had drifted apart, and nothing in the system noticed.

```
code enqueues to:      ingest, sap_push, outbound
workers listened on:   ingest, parse,    sap
```

`outbound` had **10,778 jobs** backed up and `sap_push` 10. `retry_pending_outbound`
had been firing every two minutes exactly as designed, landing on a queue no process
was reading.

This was invisible from the UI, which is the worst part. An ASN sat at `PENDING` — the
same state it holds for the thirty seconds before a real send — so the outbound tab
looked like a queue draining normally rather than a queue with no drain. The SLA monitor
could not catch it either: it flags an ACK sent late, and these were never *attempted*,
so nothing was ever marked overdue.

### Fixed

- **`worker-outbound` added** to `docker-compose.yml`, consuming `outbound`.
- **`worker-sap` repointed** from `sap` to `sap_push`, the name the code actually uses.
  This is why **"Retry SAP Push" silently did nothing**: the button returned
  `{"success": true, "message": "SAP push re-queued"}` and enqueued to a queue with no
  consumer — after already flipping the PO from `SAP_REJECTED` back to `VALIDATED`, so
  the PO looked like it was progressing while no push existed. The three orders that
  did reach B1 (3000044–46) went through `push-to-sap-with`, which runs inline.
- **Both queues drained.** Every stuck job is re-created by the next scheduler tick
  (ACK trigger 5m, backup poll 1h, retry sweep 2m, SAP push sweep 1m), so nothing was
  lost — and starting the worker on a full queue would have replayed a day of
  backlogged polls in one burst.

### The backlog was held before the worker came up, not after

Starting `worker-outbound` against the existing data would have sent 4 ASNs and
created + immediately dispatched 4 new 855 ACKs, all against fabricated smoke-test
POs. Both partner base URLs are dev (`dev.partnersbiz.com`, `silkroute.zeptonow.dev`),
so this was not going to reach a production retailer — but a shipment notice for goods
that do not exist is not something to send anywhere, and an ASN cannot be retracted
once delivered.

So the backlog was held first, using semantics the code already had rather than a new
status column:

- The 4 `PENDING` ASNs got `next_retry_at = NULL`. `enqueue_due_retries` filters on
  `next_retry_at IS NOT NULL AND <= now`, so a NULL is already "not scheduled".
- The 4 confirmed POs got a `PO_ACK_855` row pre-created and held the same way.
  `trigger_acks_for_confirmed_pos` creates an ACK *and enqueues it directly*, skipping
  the retry sweep entirely — so holding had to mean the row already existing.

Release one when it should genuinely go out:

```sql
UPDATE edi_outbound_messages SET next_retry_at = now() WHERE id = '<id>';
```

### Still outstanding

`worker-parse` consumes `parse`, and **nothing enqueues to `parse`** — every parse job
goes to `ingest` (`app/workflows/parse_and_persist.py:430`). The container has never
processed a job. Left as-is rather than repointed at `ingest`, because that would double
ingest concurrency against rate-limited partner APIs and is a separate call to make.

## Phase 7 — Blinkit ASN (856) built to contract (2026-08-20)

Invoice `LTF/26-27/001842` pushed against PO `2264110009002` (SAP Sales Order 3000046)
raises ASN `ASN-LTF/26-27/001842`, queued for Blinkit in that partner's own wire format.

Built from Blinkit's **"POVMS - ASN Sync API Contracts"** (rev 100226-093807), archived
at `_archive/backend_old/assets/POVMS-ASN Sync API Contracts-*.txt` — a 13-page PDF
despite the `.txt` extension. New module `app/adapters/outbound/blinkit_asn.py`; contract
notes in `docs/blinkit-asn-api.md`.

### A 2xx did not mean accepted, and we were treating it as one

`send_asn` returned success on any HTTP 2xx. The contract states that full acceptance,
partial acceptance **and rejection** all return 2xx, with the verdict in the body — and
its own example response pairs `"successful": true` with `"asn_sync_status": "REJECTED"`,
because the response field table defines `successful` as *"operation executed; does not
mean all items succeeded"*.

So a rejected ASN was recorded as delivered. Nobody would have found out until a truck
was turned away at the DC, with the outbound tab still showing a green send.

`interpret_asn_response()` now reads the body. It also honours the second rule that
compounds this: **a single `level: "asn"` error rejects the whole submission**, even
when item rows succeeded and even when `asn_sync_status` says `ACCEPTED`. Rejections are
deliberately not retried — a rejection is a verdict on the content, so resending the
identical body earns the identical answer.

### The previous payload builder predated the contract

`_build_blinkit_asn` in `b1_to_outbound.py` was a guess, and wrong in ways Blinkit would
have rejected or silently mis-booked:

- sent the **ASN number as `invoice_number`**
- derived `mrp` as `unit_price * 1.18` — a fabricated retail price on a tax document
- `delivery_type: "MTO"`, which is not one of the contract's `COURIER` / `SELF`
- tax keys named `cgst`/`sgst`/`igst` rather than `*_percentage`
- `uom` as a bare string where §12.19 wants `{"unit": "g", "value": 57}`
- no `tax_distribution[]` header summary, `basic_price`, `landing_price`, `quantity`,
  `item_count`, `case_config`, `batch_number`, `upc` or `hsn_code`

Both the delivery-driven and invoice-driven paths now call the same builder, so they
cannot drift into sending two shapes to one endpoint. The delivery path raises rather
than inventing an invoice number when no invoice is attached — the contract is
invoice-shaped, so there is nothing honest to send without one.

Real data now fills the payload: EANs, case sizes and MRP come from `material_master`,
`uom` is split from `grammage` (`"57g"` → `{"unit": "g", "value": 57}`),
`unit_landing_price` is line total ÷ qty so it always agrees with the invoice, and
`po_status` compares cumulative invoiced quantity across **all** invoices on the PO so
the last of several partial shipments correctly reports `PO_FULFILLED`.

All six item tax percentages are always sent, zeros included — §12.11 marks each
mandatory, and omitting a key is not the same as sending `0`.

### Noted for Blinkit

The contract contradicts itself on the supplier address: the field-detail table says
`addressLine1`/`addressLine2`, its JSON **and** XML examples say
`address_line_1`/`address_line_2`. We follow the examples, since those are the wire
format — worth confirming before go-live.

### Verification

- Payload generated from the real invoice, inspected field by field against the contract.
- Dispatch exercised end-to-end with the stored payload against a mocked endpoint:
  `ACCEPTED` → recorded sent; `REJECTED` **at HTTP 200** → recorded failed with `E108`
  surfaced. The second case is what previously passed as success.
- 317 → **342 passing**: 25 in `tests/unit/test_blinkit_asn.py`, plus the Blinkit adapter
  ASN tests rewritten — they had been asserting a fabricated response shape (`success`
  rather than the contract's `successful`/`asn_sync_status`).
- `ruff check app/` and `mypy` clean on the new modules.

**Not sent to Blinkit.** This is fabricated shipment data, and announcing a delivery that
is not happening to a live retailer system — even pre-prod — is not something to do for a
smoke test. The ASN sits `PENDING` in the outbound queue awaiting a real dispatch.

## Fix — B1 log serialization 500s once a PO was actually pushed (2026-08-19)

`GET /api/pos/{id}` returned 500 for any PO with a SAP push behind it:
`AttributeError: 'B1ApiLog' object has no attribute 'http_status'`.

Four attribute names in the API layer never matched the model:

| API field | Actual column |
|---|---|
| `http_status` | `response_status` |
| `request_payload` | `request_body` |
| `response_payload` | `response_body` |
| `success` | *(no column at all)* |

**Why it stayed hidden.** Nothing had ever been pushed to B1, so `b1_api_log` was empty.
An empty table serializes cleanly — the list endpoint returned `items: []` and looked
healthy, and PO detail never entered the loop that reads these fields. The first real
Sales Order turned four endpoints into 500s at once: PO detail, the B1 Logs list, its
detail view, and the `?success=` filter (which was a broken SQL reference, not just a
serialization one).

`success` is now a `hybrid_property` derived from the HTTP status rather than a stored
column — a column would be a second source of truth that could drift from
`response_status`, and a request that never reached B1 (logged with status 0) is
correctly not a success. Being hybrid keeps `?success=false` filtering in SQL.

The two routes now map field by field instead of `model_validate(..., from_attributes)`,
which silently required all four names to line up.

- `tests/unit/test_b1_log_serialization.py` — 17 tests against a **populated** log row,
  which is the case the old tests never covered. Also pins the reverse mistake: if a real
  `http_status` column is ever added, the explicit mappings need revisiting.
- Suite 296 → **313 passing**.

Also fixed while testing the Blinkit pipeline: `ShipToMappingRule` writes
`line.b1_whs_code` onto every line, and the mapper preferred it over the warehouse chosen
in the push dialog — so selecting a warehouse silently did nothing. Worse, the seeded
BLINKIT ship-to rows point at `WH01`/`WH02`/`WH06`, which do not exist in the real B1, so
that path would have been rejected on push. The operator's selection now wins and a
disagreement raises a visible warning.

## Phase 6 — Blinkit PO → SAP B1 Sales Order, with branch/warehouse selection (2026-08-19)

**A real Sales Order now exists in `TESTECPL260422`: DocNum 3000044 / DocEntry 1767**,
created from Blinkit PO `2264110001442` — `DocTotal 14430.00`, `VatSum 687.12`, both
lines `CSGST@5` on `FG_MH`.

The payload was rebuilt against **documents actually posted in the live company**
(`GET /Orders?$filter=CardCode eq 'D00086'`, DocEntry 1764) rather than the generic
Service Layer reference. B1 installations differ enormously in which UDFs exist and what
tax codes are called, and an undefined property fails the whole POST — so guessing the
shape from documentation would have failed on the first push with an opaque error.

### The branch is a tax decision, so the operator makes it

Under the India localization `BPL_IDAssignedToInvoice` is the **from-state** for place of
supply. Branch state == ship-to state gives `CSGST@{rate}`; otherwise `IGST@{rate}`. That
naming and rule were confirmed against ~1,600 posted lines: branch 1 (Haryana) ships
`CSGST@5` to Haryana and `IGST@5` everywhere else; branch 5 (Maharashtra) ships
`CSGST@5` to Maharashtra.

Booking a Maharashtra order against the Haryana branch produces a document B1 accepts
without complaint, with the wrong tax code, wrong ledger and wrong GST return — visible
at filing time, not at push time. So:

- **Nothing is defaulted.** The previous mapper defaulted `BPL_IDAssignedToInvoice` to 1;
  that is now an error. A default branch is a silent tax decision.
- The push dialog labels every branch with its tax effect *before* one is chosen.
- A PO whose ship-to state cannot be resolved is refused, not taxed on a guess.
- The warehouse is checked against the branch locally — B1 rejects mismatches, and a
  sentence beats relaying a Service Layer error.

### Three real bugs found on the way

- **Every B1 call would have 404'd.** `.env` set `B1_SERVICE_LAYER_URL=.../b1s/v1` and the
  client appended `/b1s/v1` again, producing `/b1s/v1/b1s/v1/Orders`. Never hit because
  B1 was never configured. `_split_base_url` now detects the suffix, so both spellings
  work and the version in the URL is honoured (this server is **v2**).
- **`query()` returned only the first OData page.** Service Layer paginates at 20 rows.
  The first warehouse sync looked like a clean success and silently produced 20 of 41
  warehouses — branches 4 and 5 appeared to have none, while posted orders used `FG_KA`
  and `FG_MH`. Now follows `@odata.nextLink`, with a page cap and a warning if hit.
- **A skipped push reported nothing.** `push_po_to_b1` returned `skip_reason` but the API
  surfaced `error`, so a status mismatch produced "SAP rejected the order" with
  `error=None` in the log — for a call that never reached SAP.

### Also

- `app/utils/gst.py` — place-of-supply resolution and B1 tax-code naming. State codes are
  read from B1's own `/States`, which uses `BH`/`OD`/`UA` where ISO-style lists say
  `BR`/`OR`/`UK`; following B1 is what matters since we compare against B1's own data.
  GSTIN prefix beats free-text state, and an unresolvable state returns `None` rather
  than a guess.
- `scripts/sync_b1_org_from_sap.py` — bootstraps Branch/Warehouse Master from the live
  company through our own sync endpoints (so the same validation, ordering rule and audit
  log apply). Reports local rows B1 does not have; `--prune` deactivates them, never
  deletes. Loaded **5 branches and 41 warehouses**.
- **`scripts/load_demo_branches_warehouses.py` deleted.** Its demo BPLIds 1–5 collide
  exactly with the real branches, so running it would have renamed Haryana to
  "Let's Try Foods — Mumbai (HO)". The eight invented warehouses were removed too.
- Migration `0013` adds `b1_bpl_id`, `b1_whs_code`, `b1_ship_to_code`, `b1_pay_to_code`
  to `edi_purchase_orders` so a retry repeats the operator's choice. Verified reversible.
- `U_MWOrderID` from the draft spec is **not defined on `ORDR`** here and is not sent.
  Confirmed against `UserFieldsMD` (236 UDFs) and a posted document. `U_OrdType`,
  `U_POEXP_DT` and `U_DC_TAT` do exist and are used.

### Dashboard

**Push to SAP** on the PO detail page now opens a dialog instead of firing a queued job:
branch (labelled CGST+SGST or IGST), warehouse (filtered to that branch), and ship-to /
bill-to addresses read live from B1 — one customer here has 142, so the ones matching the
PO's PIN or state are starred and sorted first. **Preview payload** renders the exact
JSON before anything is sent. The push runs synchronously because the operator is
watching and a B1 rejection is more useful shown than logged.

Fixed while testing: Base UI's `Select.Value` renders the raw value unless given a
formatter, so the triggers read `5` and literally `__none__`.

### Verification

- 8 endpoints exercised live; all six dispatch guards return readable 422s.
- Idempotency confirmed: a second push returns 400 naming the existing DocNum.
- Backend 270 → **295 passing** (33 new in `tests/unit/test_sales_order_mapping.py`).
  The old `TestPoToSalesOrder` class was removed — it pinned the previous contract,
  including `BPL_IDAssignedToInvoice` defaulting to 1, which is exactly what this change
  makes impossible.
- Frontend 20 → **28 passing**. `ruff check app/`, `tsc -b`, `oxlint` all clean.
- Contract documented in `docs/sap-sales-order-push.md`; four Postman requests added
  (including the negative warehouse/branch mismatch).

## Phase 8 — Branch Master and Warehouse Master REST API (2026-08-19)

Two new SAP-pushed master tables mirroring B1 `OBPL` (branch / business place) and
`OWHS` (warehouse), with the same push/read/edit shape as the rest of master data:

```
POST /api/master-data/branches/sync      GET /api/master-data/branches      PUT .../{id}
POST /api/master-data/warehouses/sync    GET /api/master-data/warehouses    PUT .../{id}
```

**These are ours, not the retailer's.** Ship-to and bill-to describe a retailer's
locations and carry an ops mapping decision (`b1_whs_code`, `b1_bill_to_code`). Branch
and warehouse describe our own SAP org structure, so there is nothing to map: SAP owns
every business field, and `is_active` / `notes` are the only locally-writable columns.
Sync never touches those two, so a push cannot undo an ops decision — and a `GET`
response can be posted straight back without erasing one.

Why hold them locally at all: a B1 Sales Order line names both a `WhsCode` and a
`BPLId`, and under the India localization the branch is the GST registration point B1
uses to derive CGST/SGST vs IGST. Reading either from the Service Layer per push would
spend licensed, capped sessions (CLAUDE.md §7) on data that changes a few times a year.

Three details that carry real risk, each pinned by a test:

- **`OWHS.BPLid` is a real FK, not a loose integer.** B1 rejects a marketing document
  whose warehouse and branch disagree, so `warehouse_master.branch_id` references
  `branch_master.id` and sync resolves it from the incoming `bpl_id`. A warehouse naming
  an unknown branch is skipped and reported rather than created with a dangling link —
  the same ordering rule that already makes SKU mapping depend on Item Master. **Push
  branches before warehouses.**
- **Re-parenting is a SAP change only.** Sending the same `whs_code` with a different
  `bpl_id` moves the warehouse; the same edit via `PUT` returns `409` naming the field.
  Allowing it locally would let the dashboard create exactly the warehouse/branch
  mismatch B1 refuses.
- **`Disabled` and `Inactive` arrive as SAP `NVARCHAR` Y/N.** Stored as booleans;
  `"Y"`/`"N"`, `true`/`false` and `1`/`0` are all accepted (Pydantic coerces them), the
  same treatment `material_master.frozen_for` already gets. A silent mis-read would
  invert a branch's status.

Both flags are always overwritten by sync, deliberately: a branch SAP has just
re-enabled must stop being treated as closed on the very next push.

Routes live in `app/api/routes/branch_warehouse.py` rather than `master_data.py`, which
was already 1,288 lines. Same `/api/master-data` prefix and OpenAPI tag, so they appear
alongside the rest in `/docs`.

- Migration `0012_branch_warehouse_master` — verified reversible (`upgrade` →
  `downgrade -1` → `upgrade` clean).
- 32 unit tests in `tests/unit/test_branch_warehouse.py`; suite 238 → 270 passing.
  `ruff check app/` clean, `mypy app/api/routes/branch_warehouse.py` clean.
- `docs/sap-master-data-api.md` → v2.1: new sections 12 and 13, endpoint summary,
  integration sequence and smoke test updated (tail sections renumbered 12–15 → 14–17).
- Postman collection: two new folders (10, 11) with working payloads and both negative
  cases; every request executed against the running API before committing. Later folders
  renumbered, so the webhook folder is now **17**, not 15.
- `docs/backlog.md` created — records validating `ship_to_mapping.b1_whs_code` against
  the new warehouse master, which is now possible for the first time.

### Dashboard screen

**Master Data → Warehouses** (`/master-data/warehouses`), reached from a nested sidebar
row under Master Data. Two tabs — Warehouses (default, filterable by branch) and
Branches — in `frontend/src/features/warehouses/`.

The screen's job is keeping **two different facts visibly apart**: SAP's own flag
(`OBPL.Disabled` / `OWHS.Inactive`, shown as *Live* / *Disabled* / *Inactive*) and ours
(`is_active`, shown as *In use* / *Parked*). A warehouse can be live in SAP and parked
here — dock under repair — and collapsing them into one column would hide which system
needs the fix. Park / Resume is the only write the dashboard makes, and it sends
`is_active` alone, since anything SAP owns comes back 409.

`Sidebar.tsx` grew nested rows. `NavLink` matching had to stay prefix-based for the
existing entries — `end` on every row would have stopped `/pos/:id` highlighting
"Purchase Orders" — so exact matching applies only to `/` and to rows that have
children.

- `scripts/load_demo_branches_warehouses.py` — 5 branches (one SAP-disabled) and
  8 warehouses (one SAP-inactive, one live-but-parked) driven through the live sync API,
  so it exercises auth, both handlers, the branch-before-warehouse ordering rule and the
  ops PUT. Idempotent; re-running reports `updated`, never `created`.
- 7 Vitest tests; frontend suite 13 → 20 passing. `tsc -b`, `oxlint` and `vite build`
  all clean.
- Response shape verified field-for-field against the TypeScript interfaces — 17 keys
  each, no drift in either direction.

## Phase 4 — Blinkit parser aligned to the POVMS contract (2026-08-19)

Rebuilt the Blinkit parser against Blinkit's own **"POVMS - Purchase Order Creation API
Contracts" (2026-02-10)**, archived at
`_archive/backend_old/assets/POVMS-Purchase Order Creation API Contracts-*.txt` — a PDF
despite the `.txt` extension. The parser docstring now cites contract section numbers
(3.6.x) so a future revision can be diffed against the code directly.

Five contract facts changed real behaviour:

- **`sku_code` (3.6.2) is optional and the contract's own example ships it EMPTY.** It is
  the field we map on, so an empty value produced a blank `buyer_sku` and an unmappable
  line. Now falls back to `item_id` (3.6.1, mandatory) with a warning, because the SAP
  mapping must then be keyed on the item_id.
- **`line_number` (3.6.3) is ZERO-BASED.** Our UI, every other partner and the
  `(po_id, line_number)` unique constraint assume 1-based. A PO numbering from 0 is now
  shifted by +1 across the whole PO — relative order preserved, only the offset moves.
- **`uom` (3.6.11) was hardcoded to `"EA"`**, silently mislabelling a 12 ml item as
  12 each — which then converts wrongly against `sku_mapping.qty_per_buyer_uom`.
- **CESS (3.6.7.4 / 3.6.7.5) was dropped entirely.** The percentage and the flat
  `additional_cess_value` are different units; both are now captured and combined onto
  the line and the header.
- **`landing_price` (3.6.5) is NOT the billing price.** It includes logistics and taxes;
  `basic_price` (3.6.6) is the pre-tax cost. Pricing on the wrong one would inflate
  taxable value and double-count tax. Documented so it is not "corrected" later.

Contract enums are now real `StrEnum`s rather than string literals: `BlinkitTenant`
(BLINKIT | HYPERPURE), `BlinkitEventType`, `BlinkitAckStatus`
(processing | accepted | partially_accepted | rejected), `BlinkitErrorCode` (E101-E105)
and `BlinkitWarningCode` (W101). The contract's enum table is lower-case while its own
example JSON shows `PARTIALLY_ACCEPTED`; we follow the table as the normative part.

Header checks added, all non-fatal — a readable PO is worth storing even when a count
disagrees, but the drift belongs on the PO rather than in a log:

- **`tenant` (section 4) may be HYPERPURE**, which is a different legal buyer from
  Blinkit with its own GSTIN and CardCode. Booking it against the Blinkit CardCode
  invoices the wrong customer, so a non-BLINKIT tenant now warns.
- `details.po_number` vs top-level mismatch (3.1), `total_sku` (3.7) and `total_qty`
  (3.8) vs what was parsed.
- **`total_amount` (3.9) divergence beyond ₹1.00.** The header value still wins — it is
  what Blinkit pays against — but it is also the figure most worth distrusting: the
  contract's own example ships `total_amount: 42` for a PO whose lines come to 7,814.52.

`hsn_code` is **not in the contract at any level**. It is still read when present, since
production payloads have carried it, but its absence is normal and not an error.

Not mapped, and deliberately so: `vehicle_details`, `buyer_details.registered_address`,
`contact_details[]`, the whole `supplier_details` block, `crates_config` and
`custom_attributes` have no canonical destination. Expanding EDI850 for partner-specific
extras would push Blinkit's shape into every other partner's documents.

**Zepto is untouched** — verified `git diff` against HEAD is empty for both
`zepto_parser.py` and `zepto_api.py`. Existing Blinkit behaviour is unchanged too: the
original and updated parsers produce byte-identical output on both production fixtures.

28 new tests pin the contract behaviours; 238 unit tests pass, `ruff check app/` clean.

## Phase 3 — Swiggy changed its attachment format; parser now handles both (2026-08-19)

**183 Swiggy POs from 2026-08-06 onward had been failing silently.** Every one reported
"No .xls attachment found".

Swiggy switched attachment generations without notice:

    LEGACY   SOTY-{SELLER}-{PO}.xls
             SpreadsheetML — XML carrying an .xls extension
    CURRENT  {CODE}_CREATE_OTB_PURCHASE_ORDER_{uuid}.xlsx
             genuine OOXML (a ZIP)

Two separate faults, either of which alone would have broken it:

1. **Detection.** The selector used `endswith(".xls")`, which does not match `".xlsx"` —
   `.xls` is a prefix of `.xlsx`, so the check reads as though it should work and does
   not. Every new-format email looked like it had no spreadsheet at all.
2. **Reading.** Even once found, `.xlsx` is a ZIP and the SpreadsheetML XML path cannot
   open it. It needs openpyxl and a genuinely different extraction: a 2D grid with a
   two-row merged header, not a flat positional cell list.

- Dispatch is on the file's **magic bytes** (`PK\x03\x04`), not its extension. Extensions
  have already proved unreliable here; content cannot lie.
- The OOXML path addresses columns by **header name**, not fixed offsets — the file
  already shifts columns between its item rows and its totals row, and Swiggy has now
  changed this layout once without warning. `_ooxml_column_map()` reconstructs the
  two-row header by carrying merged group labels forward, so `CGST`+`Amt` resolves to a
  real column.
- Grand total is read from the labelled `Grand Total (INR)` row rather than the
  unlabelled totals row above it, whose columns are offset by a merged cell — reading
  that one positionally returns the tax figure instead of the total.
- `openpyxl` is loaded **without** `read_only`: Swiggy's generator writes an inaccurate
  `<dimension>` record, and read_only trusts it, reporting a 1×1 sheet for an 87-row file.
- The "no attachment" error now lists what the email actually carried, which is what
  makes the two genuine remaining failures self-explanatory.

**Legacy path verified byte-for-byte unchanged** — the original parser and the updated
one were run against the same legacy `.xls` and produced identical output down to
per-line values.

Validated against 12 real failing files: all parsed, all totals reconciling with the
file's own grand total (largest delta ₹0.08, Swiggy's own rounding). Backfilled the
stuck messages: **FAILED 183 → 2**. The two survivors are not POs — they are GRN
documents (`GRN_159731.pdf`, `GRN_print_V2-*.pdf`) that landed in the PO label and have
no spreadsheet to parse, so failing is correct.

17 new tests cover both generations, attachment preference when both are present,
format detection, the totals-row trap, and per-row error isolation. The `.xlsx` fixture
is synthesised in-test rather than committed — the real files carry live customer
addresses and GSTINs.

## Phase 8 — Bill-to addresses (2026-08-18)

A parallel master-data resource to Ship-to, for the retailer's **invoicing** entity.

Deliberately its own table rather than a `type` flag on `ship_to_mapping`, because the
two are different things that routinely differ: goods go to a distribution centre, the
invoice goes to the registered office. When those sit in different states both are
needed — the **ship-to** state decides CGST/SGST vs IGST (place of supply, CLAUDE.md
section 8), while the **bill-to** GSTIN is what prints on the invoice as the buyer's
registration. A single combined row cannot express that, and would mis-tax the common
interstate case. The B1 target differs too: ship-to resolves to a warehouse (`WhsCode`),
bill-to to an address name on the Business Partner — hence `b1_bill_to_code`.

- Migration `0011` creates `bill_to_mapping`, keyed on
  (`trading_partner_id`, `buyer_bill_to_code`). Reuses the existing `mapping_status_t`
  enum, so no new type is created or dropped. Verified reversible: upgrade → downgrade
  → upgrade.
- `GET /api/master-data/bill-to`, `PUT .../{id}`, `POST .../bill-to/sync` — mirroring
  ship-to exactly, including the ownership split: sync writes address and GST fields
  only and never touches `b1_bill_to_code`, so a re-sync cannot undo an ops mapping.
  Confirmed by test: after mapping `ZEP-HO` and re-syncing, it stayed
  `BILLTO-HO`/`MANUALLY_MAPPED`.
- `GET /api/master-data/partners/{id}` gains `bill_to_mappings`, so the customer
  drill-down still costs one round trip.
- Master Data UI: a third sub-tab under each customer — SKU Mappings | Ship-to | Bill-to.
- Round-trip and immutability behaviour matches ship-to: GET → PUT unchanged returns
  200; changing a sync-owned field (`city`) or the identity code returns 409
  `IMMUTABLE_FIELD` rather than silently discarding the edit.
- Contract documented for the SAP team as section 11 of `docs/sap-master-data-api.md`
  (following sections renumbered), plus two requests in the Postman collection.
- The existing `MasterDataPage` test fixture had to gain `bill_to_mappings` — without it
  `data.bill_to_mappings.length` threw and blanked the whole panel, which is what the
  failing test caught.

## Phase 7 — Invoice detail, manual ASN send, PDF download (2026-08-11)

Clicking a row on the Invoices tab now opens full detail: header, e-invoicing references,
line items with a single collapsed tax column (CGST+SGST and IGST are mutually exclusive,
so three mostly-zero columns read worse than one), and a totals block.

- **Send ASN** covers both dead ends the tab surfaces. An invoice held by validation has
  no ASN — the button raises and queues one, which is an explicit operator override, so it
  is written to the audit log with `validation_override: true` and the open
  `E200_INVOICE_HELD` issues are resolved to stop the exception queue showing work that
  someone has already dealt with. An invoice whose dispatch failed instead gets its retry
  counter reset and is re-queued. Refused with `409` when the ASN is already delivered and
  acknowledged: re-sending a live 856 creates a duplicate shipment notice at the retailer,
  which is worse than the problem it would be fixing.
- **Download PDF** renders a GST tax invoice on demand. Not cached — SAP re-pushes invoices
  to add the IRN, and a stored PDF would show stale references with no signal it had aged.
  Fetched through the axios instance rather than a bare `<a href>` so it carries auth;
  otherwise the link lands on the login page instead of a file.
- New dependency **reportlab 4.2.5**, recorded in CLAUDE.md section 2. Chosen over
  weasyprint because it is pure Python — no cairo/pango to install in the image — and its
  platypus `Table` handles line-item pagination, verified on a 40-line Swiggy invoice that
  breaks across two pages with the header repeating.
- `_inr()` formats with Indian digit grouping (`12,34,567.50`); Python's own separator
  groups in threes throughout, which is wrong on an Indian tax invoice.
- Rendering the first real PDF caught a defect: `edi_purchase_orders.ship_to_address` is
  JSONB, and stringifying it put a raw Python dict —
  `{'name': 'TEST-MUM-FARUKHNAGR', 'gstin': ...}` — on the customer-facing invoice.
  `_address_lines()` now walks the known keys in postal order and tolerates the string,
  empty and non-dict shapes different parsers produce.

## Phase 7 — SAP pushes invoices; ASNs raise automatically (2026-08-11)

Invoices now arrive by SAP posting to `POST /api/invoices` rather than us polling B1 for
them — the same inversion already chosen for master data, and for the same reason:
Service Layer sessions are licensed and capped, so a recurring read against them is the
wrong shape. Supersedes the polling-first design in CLAUDE.md Phase 7, which has been
updated rather than left contradicting the code.

**Receiving an invoice raises its ASN and sends it.** No channel branching: the outbound
registry already resolves Zepto/Blinkit to their APIs and Swiggy to email from the
partner's `source_channel`, so the workflow hands over a partner-neutral payload and the
existing adapters do the rest.

**Dispatch is automatic only when the invoice validates.** Two checks gate it — the header
`grand_total` must reconcile with the sum of line totals within ₹1.00 (B1 rounds centrally,
so an exact match is not realistic), and cumulative invoiced quantity must not exceed the
ordered quantity counting every other invoice on that PO. A failure stores the invoice and
**holds** it in the exceptions queue as `E200_INVOICE_HELD`. An ASN cannot be quietly
retracted once a retailer has it, so the expensive direction of error is sending, not
waiting.

- `POST /api/invoices` — batch up to 500, idempotent on `invoice_number`. Per-invoice
  `results[]` so one bad row in a batch is actionable without guessing which.
- `GET /api/invoices`, `GET /api/pos/{po_id}/invoices` — the latter backs a new **Invoices**
  tab on PO detail showing invoice, IRN state, ASN number and whether the partner received it.
- Re-pushing is expected: B1 has no IRN when the invoice is posted, so SAP pushes once
  immediately and again when the IRP responds. A re-push never raises a second ASN.
- Polling demoted to an hourly backup (`B1_BACKUP_POLL_INTERVAL_SECONDS`) rather than
  removed — a push that fails and is never retried would otherwise vanish silently, the
  same failure mode as the Zepto `days=1` bug. The 855 ACK trigger stays at 5 minutes;
  it is SLA-bound and has no push equivalent.
- Contract for the SAP team: `docs/sap-invoice-api.md`.
- `scripts/create_dummy_invoices.py` builds test invoices for both dispatch channels,
  deriving quantities and tax from the PO's own lines so they reconcile and clear
  validation instead of testing the gate. `--dry-run`, `--json-only DIR` (Postman
  payloads, written to `tests/fixtures/invoices/`) and `--cleanup` to undo.
- Creating those dummies surfaced a real defect: `edi_outbound_messages.channel` defaults
  to `"API"` and was never set, so a Swiggy ASN was stored labelled `API`. Dispatch was
  unaffected — `send_outbound` re-resolves the adapter from `partner.source_channel` — but
  the outbound tab would have pointed anyone debugging an email failure at the API path.
  Now stamped from the partner.
- Verified end-to-end against a real PO: valid partial dispatch → ASN raised and queued;
  same invoice re-pushed → updated, no second ASN; cumulative 3+4 against 5 ordered →
  held with the running total named. 14 new unit tests; 193 backend tests pass.

## Phase 4 — Local frontend was reading the wrong backend (2026-08-10)

The API Inbox showed 4 Zepto POs locally while the server held 362. Both numbers were
correct — they are different databases, and the local dev server was never pointed at the
deployed backend.

`VITE_BASE_PATH=https://.../edi-backend/api/` had been added to the **root** `.env`. Three
things stopped it working: the SPA reads `VITE_API_BASE_URL`, not `VITE_BASE_PATH` (which
is Vite's asset sub-path); Vite only loads env files from `frontend/`, so a root-level
`.env` is invisible to it; and the trailing `/api/` would have doubled up against route
paths that already start with `/api`.

Pointing `VITE_API_BASE_URL` at the deployed host would still not have worked. The SPA
authenticates with an httpOnly cookie set `SameSite=Lax`, which browsers refuse to send on
cross-site XHR — login succeeds, then every subsequent request 401s into a redirect loop.

- `vite.config.ts` now proxies `/api`, `/auth` and `/health` through the dev server, so the
  browser only ever makes same-origin requests. No CORS entry and no backend change needed.
  `cookieDomainRewrite` re-scopes the session cookie to localhost.
- The config now reads env via `loadEnv()`. `process.env` in `vite.config.ts` sees only
  real shell variables, never `.env.local` — the same trap as the original mistake.
- Proxy fallback corrected to `http://localhost:8001`: the api container publishes on 8001
  (`API_HOST_PORT` in docker-compose.yml), while `.env.development` still pointed at 8000.
- `frontend/.env.local` (gitignored) selects the target via `VITE_PROXY_TARGET`.
- Verified: requests through the dev server return `server: nginx/1.31.2` (the deployed
  host) rather than `server: uvicorn` (local). `tsc --noEmit` clean, 12 frontend tests pass.

## Phase 4 — Zepto 428 is an IP allowlist rejection, not bad credentials (2026-08-10)

A live credential test from a dev machine returns **HTTP 428 with an empty body**. The
credentials are valid; Zepto rejects the calling IP. Because the body is empty, this used
to surface as `Expecting value: line 1 column 1` — indistinguishable from a credentials or
payload fault, which is what "invalid client credentials" was really describing.

- Added an explicit 428 branch logging `zepto.fetch.ip_not_whitelisted` and stating that
  credentials are not the cause. It returns immediately instead of spending three retries
  on a condition that cannot change mid-run.

## Phase 4 — Zepto poll window floored at 14 days (2026-08-10)

Production was polling Zepto with `days=1` on every run. `_since_to_days()` derived the
window from `now - last_fetched_at`, and because the scheduler re-polls every few minutes
the delta always rounded down to 0 → `max(1, 0+1)` → `days=1`. Confirmed in the live
worker logs: `zepto.fetch.done days=1`.

A one-day window has no margin. Any PO that Zepto backdates, publishes late, or that we
miss during an outage falls outside the window on the very next poll and is never
requested again — which is precisely how the 28 Jul POs were lost.

- Added `_MIN_LOOKBACK_DAYS = 14`; `_since_to_days()` now returns at least that floor,
  still capped at Zepto's documented max of 45. Beyond the floor the watermark delta
  still drives the window.
- Re-requesting the overlap is free: `raw_messages` is unique on
  `(trading_partner_id, external_id)` where `external_id` is Zepto's `eventId`, so
  already-seen events are skipped before reaching the parser.
- Tests: added a regression case for the just-polled watermark (the `days=1` bug) and one
  asserting the delta still governs past the floor.

Duplicate-PO safety was audited at the same time and needs no change. Three layers already
guard it: `eventId` uniqueness on `raw_messages`, `(trading_partner_id, buyer_po_number,
version)` uniqueness on `edi_purchase_orders`, and `_resolve_version()` which bumps the
version and marks the prior row `SUPERSEDED`. Production shows this working — `P368480`
exists as v1 `SUPERSEDED` + v2 `EXCEPTION`, a genuine Zepto revision, and is the only
repeated PO number across 362 rows.

## Phase 4 — Webhook 401 hint was sending partners down the wrong path (2026-08-07)

Blinkit reported `UNAUTHENTICATED: Invalid api-key` on `POST /api/webhooks/BLINKIT`. The rejection itself was correct — a `webhook_secret` is configured for BLINKIT on the server and the key they sent did not match — but the accompanying `hint` read *"Send 'Authorization: Bearer <access_token>'… call POST /auth/login again."*

That advice is impossible for a webhook partner to follow: Blinkit has no user account and webhooks authenticate with an `api-key` header, not a bearer token. The generic 401 hint in `error_handlers.py` was being applied to every 401 regardless of endpoint.

- The 401 hint is now **path-aware**: requests under `/api/webhooks/` get "Send your webhook key as the 'api-key' header…"; everything else keeps the Bearer/login hint.
- Verified both branches locally against a real configured secret: wrong key → 401 with the webhook hint, correct key → 200. Probe rows cleaned up afterwards; the local `webhook_secret` was reset to empty.

Server confirmed healthy during diagnosis: `POST /api/webhooks/BLINKIT` is live, routing works (unknown partner → 404), and auth is enforced.

## Phase 4 — Zepto fetch outage: watermark poisoning + parser rekey (2026-08-01)

**The 4 test POs Zepto pushed on 28 Jul (P368477–P368480) were missed by two stacked failures.**

The second one is the reason a fix alone was not enough: the silent-failure bug advanced
`last_fetched_at` to *now* on every failed poll for two weeks. Since the adapter derives
Zepto's `days` window from that watermark, it had drifted to `days=1` — so even from the
whitelisted server IP, the fetch would ask Zepto for "the last 1 day" and never see POs
pushed 4 days earlier. Wound the watermark back to the last genuinely successful fetch
(2026-07-17T19:39), which yields `days=15`.

- **`scripts/reset_api_watermark.py`** (new) — `--show` / `--to` / `--days-ago` / `--clear`.
  **The deployed VPS has its own database and its own poisoned watermark**; it must be reset
  there too, or the deploy alone will still return nothing.

## Phase 4 — Zepto fetch outage diagnosed and fixed; parser rekeyed to skuCode (2026-08-01)

Zepto pushed 4 new test POs that never appeared. Root-cause chain, in order:

1. **`.env` had lost `RENDER_URL` and gained `ENVIRONMENT=production` (with an inline comment)** — the adapter dropped to direct mode. Then the Render proxy itself turned out to be dead (no response in 180s), and per the decision made during the fix, **Render is now retired entirely**: the backend calls Zepto directly, and the deployed server's IP is the whitelisted one. `RENDER_URL` is intentionally unset; the proxy code path remains in the adapter, inert.
2. **Zepto rejects non-whitelisted IPs with HTTP 428 and an empty body** — which the adapter logged only as "Expecting value: line 1 column 1". Added `zepto.fetch.non_json_response` logging (status, URL, body snippet) so a block page can never masquerade as a JSON bug again.
3. **Silent-failure bug (mine): the watermark advanced on every failed fetch.** The workflow correctly gates `last_fetched_at` on `not result.errors`, but the adapter swallowed all page errors and returned `[]` — indistinguishable from "no new POs". `fetch_new_pos` now **raises** on first-page total failure; the workflow records the error and leaves the watermark alone. `test_fetch_returns_empty_on_500` asserted the old behaviour and was rewritten as `test_fetch_raises_on_total_failure`.
4. **Parser keyed Zepto lines on the wrong field.** `buyer_sku` preferred `materialCode` ("2223"), but the ops mapping sheet keys Zepto SKUs by the UUID `skuCode` — so all 105 loaded mappings would never match a PO line. Preference flipped to `skuCode`-first; the 4 stored test POs re-parsed successfully (P368265, P368264, P367071, P367067). Client timeout also raised 30s→90s.

**Local fetches still fail by design** — this machine's IP is not whitelisted; the 428 is now loud and the watermark stays put. The fetch will work from the deployed VPS.

⚠️ **Deploy-side check before expecting POs on the VPS:** if its `.env` says `ENVIRONMENT=production`, the adapter targets `silkroute.zepto.co.in` (prod), but the test POs live on QA (`silkroute.zeptonow.dev`). Set `ZEPTO_BASE_URL=https://silkroute.zeptonow.dev` there while testing against Zepto QA.

## Phase 8 — Real SKU mappings loaded customer-wise from the mapping sheet (2026-08-01)

Loaded "SAP FILLING SHEET - MAPPING.csv" through `POST /sku-mappings/sync` — 961 sheet rows → **631 mappings across 8 customers**, zero rejections (every referenced SAP item code resolved against the 182-item catalogue). Verified customer-wise via `GET /partners/{id}`: AMAZON 75, BIGBASKET 81, BLINKIT 91, FLIPKART 100, LOTS 10, RELIANCE_JIO 26, SWIGGY 143, ZEPTO 105 — each row carrying the retailer's own code (EAN / UUID / ASIN / FSN / numeric), negotiated `unit_price` (from UNIT COST) and `margin` (from Discount), with mrp/ean/case/grammage joined from Item Master.

- **`scripts/import_sku_mapping_csv.py`** (new, reusable, idempotent — re-run reports `updated=631`). `RELIANCE` in the sheet maps to our `RELIANCE_JIO`; UNIT COST `0.00`/blank is treated as *not negotiated* and omitted rather than stored as a zero PriceVarianceRule would flag every PO against.
- **LOTS partner created** via `POST /partners` (MANUAL channel, inert) — the sheet trades with LOTS Wholesale but no such partner existed. Its 10 mappings loaded; it appears in Manual Inbox until an integration is built.
- Skipped by design and reported: 245 chain-less rows (EAN-keyed reference block + blank filler), 85 rows whose SAP ITEM CODE is `#N/A` (retailer listings not yet mapped in SAP — the sheet's own unmapped backlog, largest on SWIGGY with 55). 16 rows carry a SAP ALTERNATE CODE we have no field for.

With master data now real end to end (items + mappings), an incoming Zepto/Blinkit PO can resolve its lines against actual FG codes — E002_SKU_UNRESOLVED should now only fire for genuinely unmapped listings.

## Phase 8 — Real SKU master loaded; dummy items removed (2026-07-18)

Item Master now holds the company's real catalogue, loaded from the ops team's "Master Sheet - SKU MASTER.csv" through `POST /materials/sync` — the same path SAP will use, so the load is idempotent (verified: re-run reports `updated=182`, no duplicates).

- **`scripts/import_sku_master_csv.py`** (new, reusable) — maps SAP ID→item_code, SKU SAP NAME→item_name, SKU INTERNAL NAME→frgn_name, CATEGORY→items_group_name, grammage/case/MRP/EAN/HSN; `Status: INACTIVE` → `is_active=false, valid_for=0`.
- **Loaded 182 items** (180 active + FG00161, FG00186 inactive). **53 CSV rows skipped** — every one lacks a SAP ID and is a discontinued INACTIVE line (Pratham range, gifting boxes), so nothing active was lost. No duplicate SAP IDs in the sheet.
- **Deleted the 19 dummy items** (LTF*) **and the 57 demo SKU mappings** pointing at them — the mappings had to go with the items (NOT NULL FK) and were equally fake. Checked first: zero PO line items referenced those mappings, so no transactional data was touched.
- **Not loaded:** SHELF LIFE (Day) and SKU IMAGE — Item_master has no columns for them. Shelf life is a one-migration add if wanted.

`sku_mapping` is now empty by design: real mappings arrive from SAP keyed to the FG item codes. Until then, any incoming PO will raise E002_SKU_UNRESOLVED — expected, that is the fail-loud path.

## Phase 8 — Master-data field changes: valid_for int, is_active, pan_card, POC, joined SKU fields (2026-07-18)

Four contract changes requested against the SAP-facing APIs, applied end to end (migration `0010` → models → schemas → routes → demo data → SAP doc → frontend):

- **Item Master** — `valid_for` is now an **integer** (`1`/`0`, exactly as SAP B1 sends `OITM.validFor`; migration converts in place, dropping/recreating the column default which blocks an automatic boolean→int cast). `is_active` added back as a separate boolean — the operational flag, distinct from SAP's data. Both writable on POST/PUT/sync; sync always overwrites them. The Item Master tab's status badge now reads `is_active`.
- **Customer** — `ack_sla_hours` **removed from the API surface** (create/update/response/detail). The column deliberately stays: `send_outbound.py` and `dashboard.py` read it for SLA monitoring, so dropping it would have broken Phase 7 SLA checks — it just isn't SAP's field to manage. `pan_card` (varchar 10) added across create/update/sync/response; shown in the customer drill-down where Ack SLA used to be.
- **SKU Mapping GET** — `ean_code`, `case_size` and `grammage` now joined from Item_master alongside `mrp`, on both the list endpoint and the customer drill-down. Drill-down table gained an EAN column, grammage under the item name, case size under the item code.
- **Ship-to** — `poc_name` / `poc_email` / `poc_phone` added (naming normalised from the request's "poc, mail, contact number"). Writable by **both** sync and PUT — contact info drifts and ops may fix it locally, unlike the address/GST block which stays sync-owned. Drill-down gained a POC column.

Named explicitly: "pencard" was implemented as `pan_card` (PAN, 10 chars).

Verified live: `valid_for=1/is_active` on LTFX fixtures (frozen and inactive edge cases both render), BLINKIT returns `pan_card` and no `ack_sla_hours`, SKU rows carry ean/case/grammage, ship-to carries POC; PUTs for all three checked including restore. Migration up/down/up clean. 177 unit + 17 integration tests, tsc, vitest 12/12, pinned ruff — all green. SAP doc updated in 13 places, including the §12 smoke test (`valid_for: 1`).

## Phase 8 — Inbox partner endpoints aligned with the master-data convention (2026-07-18)

The three inbox partner lists each had their own response shape: bare JSON arrays, and two near-duplicate schemas (`InboxPartnerSummary` with `gmail_label`, `ApiPartnerSummary` without). Aligned all of them with `/api/master-data/partners`:

- **One envelope everywhere** — `/api/inbox/partners`, `/api/api-inbox/partners` and `/api/manual-inbox/partners` now return `{items, total, limit, offset}` and accept `limit` (default 50, max 200) / `offset`, exactly like the master-data lists.
- **One schema** — `ApiPartnerSummary` deleted; all three use `InboxPartnerSummary`, with `gmail_label` defaulting to null (only meaningful for EMAIL partners).
- Frontend fetchers unwrap `.items` inside the api modules, so no page component changed; `ApiPartner` gained the `gmail_label` field.

**Note: this is a breaking change to the three partner endpoints** (array → envelope). The local dev frontend picks it up via the volume mount; the deployed frontend and backend ship together through CI, so they stay in step — but any cached/old frontend build against the new backend would show empty partner panels until refreshed.

Verified live: all three endpoints return the four envelope keys, totals partition as 9/5/1, and `offset=2` pages correctly. Pinned ruff clean, `tsc` clean, vitest 12/12.

## Phase 8 — Inbox partitioning: label decides the inbox; Manual Inbox added (2026-07-18)

Formalised the rule that `source_channel` decides which inbox a partner appears in. Previously the two inbox screens used different rules — API Inbox filtered on channel but Email Inbox filtered on `gmail_label IS NOT NULL` — so the tabs did not partition the partner list: RELIANCE_JIO (PORTAL) and any MANUAL partner appeared in **no** inbox, an EMAIL partner without a label would silently vanish, and a PORTAL partner *with* a label would wrongly show under Email.

- **Email Inbox** (`/api/inbox/partners`) now filters `source_channel = EMAIL`. An EMAIL partner with no `gmail_label` shows up visibly unconfigured instead of disappearing.
- **Manual Inbox** (new): `GET /api/manual-inbox/partners` lists `MANUAL` + `PORTAL` partners. PORTAL lives here deliberately until Phase 9 scraping exists — those orders are effectively manual today, and this gives RELIANCE_JIO a home. Only the partner list is new code: message listing/detail/retry reuse the `/api/inbox/messages*` routes, which were already partner-scoped and channel-agnostic, rather than growing a third copy of that logic.
- **Frontend**: `features/manual-inbox/` (partner panel + message panel, PORTAL rows labelled "handled manually until scraping is built", empty states pointing at the upcoming manual sales-order form), `/manual-inbox` route, "Manual Inbox" sidebar entry. Message clicks route to the existing `/inbox/{id}` detail page.

The three tabs now partition the active partner list exactly: EMAIL(9) + API/WEBHOOK(5) + MANUAL/PORTAL(1) = 15, verified live. This is the groundwork for the manual sales-order form (create an order for a MANUAL/PORTAL partner by picking from its synced SKU mappings), which is the next piece.

Verified: pinned ruff clean, 177 unit tests pass, `tsc`/`oxlint`/vitest (12) pass, all three partner endpoints checked live, and the reused messages endpoint returns 200 for a PORTAL partner.

## Phase 8 — CI fixes: UP038, duplicate route, and three broken model tests (2026-07-18)

CI failed on the deployed commit. Fixing it surfaced a gap in how I had been verifying work all session.

- **UP038** — `isinstance(detail, (dict, list))` → `isinstance(detail, dict | list)` in `app/api/error_handlers.py`.
- **F811 duplicate `sync_sku_mappings`** in `app/api/routes/master_data.py` — present at lines 728 *and* 821 in commit `729673e` (what CI built), already resolved in the current commit. FastAPI matches the first registration, so the second was dead code rather than a behaviour change, which is why testing never caught it.
- **Three `TestSkuMapping` integration tests fixed** — they still passed `mapping_status=` to `SkuMapping`, removed by migration `0009`, so they raised `TypeError` at construction. `test_unmapped_sku_no_material` was inverted into `test_material_id_is_required`: its premise (a mapping row with `material_id=None`) is exactly what `0009` made impossible, so the test now asserts the database rejects it.

**Root cause of missing UP038 locally: a ruff version mismatch.** `pyproject.toml` pins `ruff==0.8.4` for CI; the local binary is `0.15.20`, where UP038 has since been removed. Every "ruff clean" reported this session was from the newer, more permissive version. Verification now runs the pinned version from a dedicated venv, which reproduces CI exactly.

**The integration tests had also never been run locally** — they need a Postgres on `localhost:5433` with the credentials CI's service container provides, which does not exist here. Ran them by creating the test database, forwarding the port into the API container, and patching the connection string in a throwaway copy (the repo file is unchanged). That is what exposed the three failures.

Full CI parity now green: `ruff check app/` (0.8.4) clean, 177 unit + 17 integration tests pass, `npm run typecheck` / `lint` / `test` / `build` all pass.

## Phase 8 — SAP master-data API document rebuilt (v2.0) (2026-07-18)

`docs/sap-master-data-api.md` rewritten from the live contract rather than patched further. The v1 document had accumulated eight rounds of changes and no longer matched the API: it predated `POST /partners`, `PUT /materials/{id}`, the round-trip tolerance, the error envelope, and the batch-wrapper rules.

Rebuilt against the running instance's OpenAPI schema, covering all 14 master-data endpoints:

- **§5 Rules that apply everywhere** — Content-Type, batch wrappers per endpoint, "a 200 does not mean every row was accepted", snake_case + unknown-field rejection, sync-is-not-a-mirror, GET→POST round-tripping, and the type conventions (decimals as strings, leading zeros as strings, booleans not Y/N).
- **§6 Errors** — the single envelope, indexed field paths (`mappings[1].b1_item_code`) for locating a failing row inside a 2000-row batch, and the full status/code table.
- **§7–10** — one section per table: create/update/read, full field tables with types and requiredness, and the rules that bite (customer sync being update-only, Item Master before SKU Mapping, `frozen_for`/`valid_for` always overwritten, `state` driving the CGST/SGST-vs-IGST split, sync never touching `b1_whs_code`).
- **§11 sequence** and **§12 smoke test**, including two negative tests whose failure would indicate the safety checks are broken.

Verified before publishing: every field in `TradingPartnerCreate`, `TradingPartnerSyncItem`, `MaterialMasterSyncItem`, `SkuMappingSyncItem` and `ShipToMappingSyncItem` cross-checked against the live OpenAPI schema (all present, all required fields documented), and the full create→sync→items→mappings→ship-to→verify sequence executed end to end on a throwaway `DOCDEMO` partner, with both rejection paths confirmed. Test data cleaned up.

Still to fill before sending: the staging/production hosts in §2 and the service-account credentials in §3.

## Phase 8 — source_channel is editable on PUT /partners/{id} (2026-07-18)

`source_channel` was locked as immutable on the grounds that changing it "would re-point every PO, raw message and mapping attached to this partner." **That justification was wrong.** `raw_messages` carries its own `source_channel`, stamped at ingestion — verified against live data (182 DMART, 372 SWIGGY, 4 ZEPTO rows, each storing its own value independently of the partner). Changing a partner's channel rewrites no history; it governs future routing only.

The lock also contradicted the onboarding flow added one step earlier: `POST /partners` deliberately defaults to `MANUAL` so a new partner sits inert, and the documented next step is switching it to a live channel — which the PUT then refused.

- `source_channel` now editable on `PUT /partners/{id}`, validated against the enum (`422` listing allowed values on a bad one).
- `code` and `id` remain immutable, with a corrected reason: `code` is embedded in the webhook URL, is SAP's sync match key, and files every stored document.
- PUT now returns the same `warnings` array as create (missing parser, `EMAIL` without `gmail_label`, `WEBHOOK` without `webhook_secret`, inert `MANUAL`), via a shared `_partner_write_response()` so the two endpoints cannot drift. `TradingPartnerCreateResponse` renamed `TradingPartnerWriteResponse`.
- `webhook_secret` and `asn_sla_hours` added to the updatable set — both were previously unreachable, so a WEBHOOK partner could never be given its secret through the API.

Verified: the reported payload returns 200 with `source_channel: "API"`; switching to `EMAIL` without a label warns; `FTP` returns 422; a `code` change still 409s; and raw_message channel counts are unchanged after two channel switches. Doc §10.0 added.

## Phase 8 — POST /partners: partner onboarding endpoint (2026-07-18)

Partners could only be created by editing `scripts/seed_master_data.py` or inserting a row by hand — there was no API path at all, so `POST /partners/sync` (update-only) reported `skipped` for anything new and there was nowhere to go from there.

- **`POST /api/master-data/partners`** — creates a partner; `code` + `name` required, everything else optional. Returns **201** with the row and a `warnings` array. `409` if the code exists (pointing at the PUT), `422` on an invalid `source_channel` listing the allowed values.
- **Sync stays update-only.** Kept deliberately: SAP's customer list is mostly not EDI trading partners, so a bulk push must never mass-create rows here. Creating one is an explicit, per-partner act — which is the carve-out CLAUDE.md's "never invent partners" rule was really aimed at.
- **`source_channel` defaults to `MANUAL`**, the only inert state: the scheduler polls `API` partners and `EMAIL` partners *with* a `gmail_label`, so a `MANUAL` partner takes master data without generating failed polls. Verified `_get_api_partners()` / `_get_email_partners()` both exclude a freshly created partner.
- **`warnings` closes the gap between "row exists" and "POs flow"** — flags a missing parser for that code, `EMAIL` without a `gmail_label`, `WEBHOOK` without a `webhook_secret`, and the inert `MANUAL` state. Without this, a created partner looks ready when nothing can actually read its documents.

Verified end to end: create `BIGBAZARJIO` → 201 with both warnings; `POST /partners/sync` on the same code → `updated: 1` (was `skipped: 1`); SAP's name, CardCode and email all landed. Doc §6.0 added.

## Phase 8 — Sync endpoints accept round-trip payloads (2026-07-18)

Posting a record straight back from its `GET` response failed on every sync endpoint: the GET shapes are wider than the sync schemas, and `extra="forbid"` rejected the difference. Reported against `/partners/sync`, where a payload copied from `GET /partners` produced five "unknown field" errors for `source_channel`, `gmail_label`, `ack_sla_hours`, `created_at` and `is_active`.

- All four sync item schemas now accept the keys their GET counterpart returns (`id`, `trading_partner_id`, timestamps, plus per-table extras). They are **dropped before any write** via `_SYNC_READ_ONLY` / `_SHIP_TO_READ_ONLY`, so sync still cannot touch integration config or the ops-owned `b1_whs_code` / `mapping_status`. Verified: posting `b1_whs_code: "HACKED"` through `/ship-to/sync` leaves the stored value at `WH02`.
- `is_active` accepted as an alias of `status` (`status` wins when both are sent). `status` became nullable so an omitted value is distinguishable from an explicit `false`.
- Batch-wrapper hint added to the validation handler: posting a bare record to a `/sync` endpoint previously reported only *"field 'mappings' is missing"*, which never said the body needs wrapping. Now returns the exact wrapper for that endpoint. Fires only when the body is genuinely an unwrapped record — a real missing field inside a correct batch still reports `mappings[0].b1_item_code` with the normal hint.

**Bug caught during this change:** widening the schemas meant `item.model_dump()` in the materials and ship-to sync loops would have `setattr`'d the new keys — letting a sync overwrite `id`, `b1_whs_code` and `mapping_status`, the exact thing those endpoints are designed to protect. Excluded explicitly before the write.

## Phase 8 — PUT endpoints accept round-trip payloads (2026-07-18)

`PUT /materials/{id}` rejected `item_code` outright, which broke the ordinary GET → edit → PUT flow: a client sending back the object it just read got *"unknown field — check the spelling"* for a field that is neither unknown nor misspelled. The immutability rule was right; enforcing it by rejecting the field's mere presence was not.

- **Identity and read-only fields are now accepted on all three PUTs and ignored when unchanged**, so round-tripping works. Changing one returns `409 IMMUTABLE_FIELD` with `sent` vs `current` and the reason — never a silent discard, which would look like a successful edit that did nothing.
- Applied consistently, because the three PUTs previously disagreed: materials errored on extra fields while partners and ship-to **silently ignored** them (no `extra="forbid"`). All three now: accept identity fields, reject genuine typos, reject real changes to immutable fields.
- Ship-to additionally guards the sync-owned address/GST block — those come from `POST /ship-to/sync`, so changing `city` here returns `409` pointing at SAP rather than dropping the edit. `b1_whs_code` became optional so a partial update is possible.
- **Found while testing:** the same data has different field names depending on the endpoint — `GET /ship-to` returns `address_line`/`gst_registration_no`/`buyer_warehouse_name`, while the nested array in `GET /partners/{id}` calls them `address`/`gst_regn_no`/`warehouse_name`. I had built the update schema from the nested names, so the round-trip 422'd. Aligned to the `GET /ship-to` names. The underlying inconsistency remains and is worth unifying.
- Error handler renders structured `HTTPException(detail={...})` into `details` instead of `str()`-ing a Python dict into the message.

## Phase 8 — PUT /materials/{id} (2026-07-18)

- **`PUT /api/master-data/materials/{material_id}`** — partial edit of one item; only the fields sent are written. Accepts every field from the sync schema **except `item_code`**, which is deliberately immutable: it is the natural key SAP syncs on and the target of every SKU mapping's `b1ItemCode`, so editing it would orphan those rows. Retire with `valid_for: false` and create a replacement instead. Audit-logged with `mode="json"` so Decimal fields cannot break the JSONB write.
- Errors: unknown id → `404 NOT_FOUND`; `{}` → `422` "No fields to update"; any unknown key (including `item_code`) → `422` naming it. Added `400` and `422` to the error handler's code map so a route-raised 422 reports `VALIDATION_ERROR` rather than the generic `HTTP_ERROR`.
- **SKU mappings still have no PUT** — unchanged from the earlier decision that SAP is their sole author.
- Frontend: removed `updateSkuMapping()`, which was dead code still pointing at the removed `PUT /sku-mappings/{id}` (a 404 waiting to happen); added `updateItem()`.

Master-data write surface is now: `POST .../sync` (bulk upsert, all four tables) plus single-record `POST`/`PUT` on materials and `PUT` on partners and ship-to. Doc §10 rewritten accordingly.

## Phase 8 — Global error handlers + three POST /materials bugs (2026-07-18)

Triggered by a Postman 422 (`"Input should be a valid dictionary or object to extract fields from"`) that never mentioned its own cause. Reproducing it uncovered that the endpoint was broken three ways underneath.

- **`app/api/error_handlers.py`** (new) — one envelope for every failure: `{error: {code, message, details[], hint, request_id}}`.
  - `RequestValidationError` — detects the body-arrived-as-text case and returns **415** naming `Content-Type` explicitly, with the Postman fix in `hint`, instead of a pydantic message that never mentions headers. Field errors render as `mappings[1].b1_item_code` so a failing row is identifiable inside a 2000-row batch, with a plain-English `problem` per pydantic error type and the offending input echoed back (truncated at 200 chars).
  - `IntegrityError` → **409**, distinguishing unique violations from FK violations and pointing at the sync ordering.
  - `StarletteHTTPException` → same envelope; 401 explains the Bearer header and 8-hour expiry.
  - Catch-all `Exception` → **500** with a `request_id`; the traceback is logged server-side and never returned, so internal paths and SQL cannot leak.
- **Unknown fields now rejected** (`extra="forbid"`) on all SAP-facing request models. `itemCode` instead of `item_code` was previously accepted and silently ignored — an integration could appear to succeed while writing nothing.

**Three pre-existing bugs in `POST /api/master-data/materials`, all masked by the Content-Type error:**

1. `create_material` still referenced `body.b1_item_code` after the rename to `item_code` — **the endpoint returned 500 for every request**. Same class as the two `master_data.py` misses fixed earlier; this one was in the request-schema path rather than the ORM path.
2. `MaterialMasterCreate` exposed only 4 of 18 Item_master fields, so `tax_rate`, `mrp`, `ean_code`, `case_size` and the rest were **silently dropped** — a caller sending the documented payload would have created a half-empty item and seen 201. Now mirrors the full field set.
3. Widening it then exposed a latent bug: `audit_log.payload` is JSONB and `model_dump()` emits `Decimal`, which is not JSON-serializable — the insert failed. Both audit payloads now use `model_dump(mode="json")`, so adding Decimal fields to a schema can never break the audit write again.

Verified against the reported payload: wrong Content-Type → actionable 415; correct Content-Type → 201 with all 18 fields persisted; duplicate → 409; missing field → 422 naming it; camelCase typo → 422 listing each unknown key; batch error → indexed path. Doc §5.3–5.5 updated with the real responses.

## Phase 8 — SAP master-data API doc + Bearer token auth (2026-07-18)

- **`docs/sap-master-data-api.md`** — integration guide for the SAP B1 team covering all four master-data tables: auth, endpoint reference, per-field tables mapped to the schema's own names (`customerCode`, `itemCode`, `buyerSKUCode`, `dcCode`…), example payloads, sync-result semantics, error codes, ordering constraints, and a copy-paste smoke test. Every request example in it was executed against the running API and the documented responses are actual output, not illustrative.
- **Bearer token auth added** (`app/api/routes/auth.py`). Auth was cookie-only: `POST /auth/login` set an httpOnly `edi_token` cookie and `get_current_user_email` read `request.cookies` exclusively. Workable for the browser SPA, but it forced any server-to-server caller to scrape `Set-Cookie` and replay it, re-authenticating every 8 hours. Login now also returns `access_token` in the body and the auth dependency accepts `Authorization: Bearer <token>`, header taking precedence. The cookie path is untouched, so the SPA is unaffected — `postLogin` just unwraps `.user` from the new `LoginResponse`.
- Verified with the cookie jar removed entirely: all four master-data GETs plus `/auth/me` return 200 on Bearer alone, no-auth returns 401, and a real `POST /materials/sync` succeeds over Bearer.

**Documented as an open question for SAP, not silently assumed:** whether a multi-state retailer is one Business Partner or one per state GSTIN. `trading_partners.b1_card_code` is a single column and assumes the former; if SAP uses the latter the CardCode has to move onto `ship_to_mapping` and be chosen per PO from the delivery state. Raised in §14 of the doc.

## Phase 5/8 — SKU_Mapping is SAP-owned; auto-mapping removed (2026-07-18)

`b1ItemCode [not null]` in the master-data schema means every SKU_Mapping row is a confirmed mapping by construction. Reshaped the system around that.

- Migration `0009` — dropped `sku_mapping.mapping_status` and `confidence_score`; `material_id` is now `NOT NULL`. Rows with a null material_id (created by the old sync) were deleted, with referencing PO lines detached first so the FK could not cascade into transactional data.
- **Auto-mapping deleted** from `SkuMappingRule`: the cross-partner EAN reuse and the `rapidfuzz` fuzzy-description match (≥0.85) are gone. A 0.86 match between "Salted Almonds 100g" and "Salted Cashews 100g" would have posted a Sales Order for the wrong product. The rule is now exact lookup → `E002_SKU_UNRESOLVED` if absent. CLAUDE.md Phase 5 §4–5 rewritten to match.
- **No local writes**: removed `PUT /api/master-data/sku-mappings/{id}` and `POST|GET /api/sku-mapping` (exceptions). An unresolved SKU is fixed in SAP and re-synced, so a later sync cannot overwrite an ops edit. Read-only listing lives at `GET /api/master-data/sku-mappings`.
- `POST /sku-mappings/sync` now **requires `b1_item_code`** and rejects any row whose item code is absent from Item_master, rather than storing it half-resolved — the schema marks that ref *"no cascade — fail loud on unmapped item"*. Verified: both reject paths fire (`unknown partner code`, `item 'NO_SUCH_ITEM' not in Item_master`).
- SKU views now carry `created_at`, `updated_at` and **`mrp` joined from Item_master** (item data, not customer-specific). Inner join, since `material_id` is non-null.

**Three bugs found by loading real test data — all pre-existing, none caught by lint or typecheck:**

1. `master_data.py:440` — `row.b1_item_code` on a SELECT aliasing `item_code`: **`GET /sku-mappings` returned 500**, SKU list entirely broken.
2. `master_data.py:491` — `material.b1_item_code` ORM attribute: `PUT /sku-mappings/{id}` would have crashed on every save. Only reachable after fixing #1.
3. `dashboard.py` — `EdiPoLineItem.mapping_status` *and* `.description`, neither of which exists: **`GET /api/dashboard/unmapped-skus` had never worked** (the Dashboard's "Unmapped SKUs" card). Now queries `sap_material_no IS NULL` against the real column names and returns 52 unresolved SKUs.

**`npm run typecheck` was a no-op.** `tsconfig.json` uses `"files": []` with project references, so `tsc --noEmit` checked **zero files** — every "typecheck clean" reported during this work was vacuous. Switched the script to `tsc -b --force` (what `npm run build` already used); it immediately caught a stale test fixture. CLAUDE.md's "Definition of Done" cites `npm run typecheck`, which now actually checks.

## Phase 8 — Master Data rebuilt against the Customer/Item_master schema (2026-07-18)

**Data reset.** Catalogue tables cleared and reloaded: `material_master` (186 rows), `sku_mapping` (700), `ship_to_mapping` (5). `trading_partners` (15) and all transactional data were **kept** — `edi_purchase_orders` (549), `edi_po_line_items` (8,001) and `raw_messages` (549) all FK into master data, so a blanket delete would have destroyed them. 7,535 line items had `sku_mapping_id` nulled first so they survive as unmapped and re-resolve against the new catalogue. Pre-wipe dump saved to `_backups/master_data_before_wipe_*.sql`.

**Repo↔DB consistency repair.** The DB was stamped at revision `0005` while the `0005` migration file was missing from the repo (lost in a revert), leaving the ORM on `buyer_warehouse_code` and Postgres on `buyer_whs_code` — the ship-to code paths were broken and `alembic downgrade` could not run. Migration `0005` recreated to match the applied schema exactly.

**Schema now mirrors the master-data diagram.**
- `0005` — `trading_partners` + `business_type`/`group_name`/`phone_numbers[]`/`email_address`; `material_master` + the OITM columns; `sku_mapping` + `unit_price`/`margin`; `ship_to_mapping` renamed `buyer_warehouse_code`→`buyer_whs_code`, + `is_active` and the structured address/GSTIN block (`state` drives CGST/SGST vs IGST, CLAUDE.md §8, and previously had nowhere to live).
- `0006` — `sku_mapping.is_active` (the schema's `status`), kept distinct from `deleted_at` and `mapping_status` since the three mean different things.
- `0007` — `material_master` renamed to mirror `Item_master` 1:1: `b1_item_code`→`item_code`, `description`→`item_name`, `uom`→`invntry_uom`, `sales_uom`→`sal_unit_msr`, `vat_group_purchase`→`vat_group_pu`, `vat_group_sales`→`vat_group_sa`, `gst_rate`→`tax_rate`, `hsn_code`→`hsn`, `ean`→`ean_code`, `is_active`→`valid_for`. All call sites updated (validators, exceptions route, mappers, seed + import scripts, tests). `uom_group` and `case_size` deliberately retained — not in the diagram but they back the UoM conversion and `CaseSizeRule` respectively.
- `0008` — index on `sku_mapping.material_id`, the equivalent of the schema's `SKU_Mapping.b1ItemCode` index (we model that reference as a UUID FK, not a varchar code). Backs the customer drill-down join, which runs on every row expand.
- Deliberately **not** added, per review: `SKU_Mapping.partnerName` (derivable from the customer join) and a second `Item_master.status` alongside `validFor` (collapsed into `valid_for`).
- **FKs deliberately left `NO ACTION`** rather than the `ON DELETE CASCADE` the diagram specifies. Every one of these tables uses `deleted_at` soft-delete (CLAUDE.md §4 "never hard-delete"), so a hard `DELETE` never fires and the cascade would be dead code — while making an accidental hard delete destroy `sku_mapping` rows that `edi_po_line_items` still references.

**Master Data UI rebuilt — two tabs instead of four.**
- **Customers / Partners** — one row per customer showing customerCode/Name/BusinessType/Group/Email/Channel/Status. Expanding a row calls the new `GET /api/master-data/partners/{id}` and shows that customer's **SKU Mappings** and **Ship-to Addresses** arrays in sub-tabs (one round trip, not two screens).
- **Item Master** — full OITM grid using the schema's field names.
- Old 4-tab layout and its `SkuMappingsTab.test.tsx` removed; replaced with `MasterDataPage.test.tsx` covering both tabs and the drill-down.
- Fixed: frontend was calling `/api/master-data/ship-to-mappings` while the route is `/ship-to` — that tab was silently 404ing. Update verbs switched PATCH→PUT.

**SAP-push sync endpoints** (`POST .../sync` for partners/materials/sku-mappings/ship-to) so SAP pushes master data in rather than the middleware querying Service Layer on every read (sessions are licensed and capped, CLAUDE.md §7). Sync and manual PUT write disjoint field sets, so a sync never clobbers an ops mapping decision (`material_id`, `mapping_status`, `b1_whs_code`). Partner sync is update-only — unknown codes are skipped and reported, since onboarding needs a `source_channel` decision SAP cannot express.

Verified: `0005`→`0007` upgrade/downgrade/upgrade clean; seed reloaded; `GET /materials`, `GET /partners`, `GET /partners/{id}` (5 SKU mappings + 2 ship-to addresses) all returning the schema's field names; `ruff` clean on every changed file; `tsc --noEmit` and `oxlint` clean.

Frontend tests pass: **3 files / 12 tests in 3.5s**, including the new `MasterDataPage.test.tsx` (two-tab layout, customer drill-down loading SKU + ship-to arrays, Item_master field names). Note for anyone hitting this later: vitest's *first* run in a fresh checkout has to build its transform cache and can take ~30 minutes, during which short-bounded runs die at vitest's own 60s worker-startup timeout and print "Failed to start forks worker" — that is a cold-cache artifact, not a broken suite. Subsequent runs are seconds.

## Phase 0 — CI/CD Deploy Fixes (2026-07-27)
- `pyproject.toml` — removed `types-bcrypt==4.0.0.20240106` dev dependency; the package doesn't exist on PyPI and was breaking `pip install -e ".[dev]"` in CI. `bcrypt` ships its own inline types, so no stub package is needed.
- `docker-compose.yml` — removed public port mappings on `postgres` (`5433:5432`) and `redis` (`6379:6379`); both are only reached by other containers over the internal Docker network and had no reason to be exposed on the VPS host.
- Untracked all `__pycache__/`/`*.pyc` files (already covered by `.gitignore` but committed before the rule existed).

## Phase 4 — Zepto + Blinkit Live Integration (2026-07-17)
- `GET /api/api-inbox/status` — per-partner connection health: last_fetched_at (watermark), last_message_at, 24h message/failure counts, `is_configured` flag (checks env credentials), webhook URL for BLINKIT
- `POST /api/api-inbox/trigger-fetch?partner_code=ZEPTO` — manually enqueue an immediate Zepto poll without waiting for the 5-minute scheduler cycle; returns RQ job_id; returns 400 for WEBHOOK-only partners
- `ApiPartnerStatus` schema added to `app/schemas/api.py`
- `frontend/src/features/api-inbox/api.ts` — added `fetchApiPartnerStatus()` and `triggerFetch()` calls
- `ApiInboxPage.tsx` — added `ConnectionStatusPanel` at top showing per-partner: online/offline icon (green Wifi vs red WifiOff), push vs poll badge, last fetch time, 24h event count, "Fetch Now" button (disabled if credentials missing or already queued); panel auto-refreshes every 60s via TanStack Query

## Phase 4 — Zepto API Inbox (2026-07-17)
- `app/api/routes/api_inbox.py` — new router at `/api/api-inbox/*` for API/webhook-based partners:
  - `GET /api/api-inbox/partners` — active partners with source_channel in ('API', 'WEBHOOK') plus per-partner message counts
  - `GET /api/api-inbox/messages` — paginated raw messages; search by PO number or external_id; date filters in IST
  - `GET /api/api-inbox/messages/{id}` — detail with full `payload` JSON field (Zepto's raw PO event dict)
  - `POST /api/api-inbox/messages/{id}/retry-parse` — re-queue failed parse jobs
- `app/schemas/api.py` — added `ApiPartnerSummary` and `ApiMessageDetail` schemas
- `app/main.py` — registered `api_inbox_router`
- `frontend/src/features/api-inbox/api.ts` — typed API client for `/api/api-inbox/*` endpoints
- `frontend/src/features/api-inbox/ApiInboxPage.tsx` — two-panel layout (platform list + event list with eventId, PO number, parse status, date filters, pagination); mirrors InboxPage pattern
- `frontend/src/features/api-inbox/ApiInboxDetailPage.tsx` — detail view: metadata card, linked PO card, raw JSON payload viewer (formatted `<pre>`)
- `frontend/src/router.tsx` — added `/api-inbox` and `/api-inbox/:messageId` routes
- `frontend/src/components/layout/Sidebar.tsx` — added "API Inbox" nav item with Zap icon
- Re-implemented Zepto adapter based on `_archive/backend_old/app/services/zepto.py`

## Phase 8 — Vitest Tests (2026-07-17)
- Installed `vitest`, `@testing-library/react`, `@testing-library/user-event`, `@testing-library/jest-dom`, `jsdom` as dev dependencies
- Added `"test": "vitest run"` and `"typecheck": "tsc --noEmit"` scripts to `frontend/package.json`
- Configured Vitest in `vite.config.ts` (jsdom environment, globals, setup file, thread pool)
- Created `src/test/setup.ts` (jest-dom matchers) and `src/test/utils.tsx` (test wrapper with `QueryClientProvider` + `MemoryRouter`)
- 11 tests across 3 files, all passing:
  - `POListPage.test.tsx` — renders rows, loading state, URL-synced `po_status` filter, empty state
  - `SkuMappingsTab.test.tsx` — renders unmapped SKU, shows inline edit inputs, calls `updateSkuMapping` with correct args
  - `ExceptionsPage.test.tsx` — renders grouped exceptions, opens resolve dialog, calls `resolveException` + re-fetches list, empty state

## Phase 8.1 — Inbox Search/Date Filters + PO Received-At (2026-07-15)
- `GET /api/inbox/messages` — added `search` (matches PO number or email subject via JSONB `headers.subject`), `date_from`, and `date_to` query params; dates compared in IST timezone
- `InboxPage.tsx` — search box (350ms debounce → URL param), date-range pickers, "Clear" button; filter state URL-synced so page is bookmarkable; pagination preserved across filter changes; empty state distinguishes filtered vs unfiltered
- `InboxDetailPage.tsx` + `/inbox/:messageId` route — individual email detail view with attachment download, parse retry, link to canonical PO
- `GET /api/pos` — `received_at` column added (coalesces `RawMessage.received_at` → `EdiPurchaseOrder.created_at`); PO list now sorted by received time; date filters use received_at; `version` field added for PO revision display
- `POListItem` schema + `POListItem` TypeScript type updated with `received_at: datetime`
- PO list "Received" sortable column (replaces created_at); version chip (`v2`) shown on revised POs
- `SUPERSEDED` and `RECEIVED` statuses added to PO list filter dropdown and StatusBadge config
- Fixed `E741` ruff warning in `swiggy_parser.py` (ambiguous variable `l` → `part`)

## Phase 3.5 — PO Revision Flow (2026-07-14)
- Migration `0004` — added `SUPERSEDED` to `po_status_t` enum
- When a partner re-sends a PO with the same PO number (revised qty/SKU/expiry after e.g. a case-size rejection), the new email now creates **version N+1** of the PO and the previous version is marked **SUPERSEDED** (read-only, hidden from exceptions queue, blocked from edit/SAP push)
- Revision matching window: **25 days** (same PO number older than that = unrelated PO reusing the number; version still bumps for the unique constraint but old PO stays active)
- Re-parsing an email that already produced a PO now links to it instead of creating a fake revision
- `version` exposed in PO list + detail APIs; UI shows `v2` chip next to the PO number, "Superseded" badge, and hides all action buttons on superseded versions
- Verified live: duplicate GWAPO36356 email created v2 and superseded v1 with a status-history note naming the new version and source email
- Fixed "Failed to load PDF document" — Cloudinary blocks public delivery of PDFs (401). Added `GET /api/inbox/messages/{id}/attachments/{index}` which streams the file through the backend using a signed private-download URL (auth-protected by our login); inbox attachment "Open" button now fetches via this proxy; PO Raw Source tab's dead `/api/raw-messages` link now points to the source email page

## Phase 5 — Real Master Data + Case Size & SKU Validation (2026-07-14)
- Migration `0003` — added `case_size`, `ean`, `mrp` columns to `material_master`
- Created `scripts/import_master_data.py` — imports real master data from `docs/Mapping.xlsx` (692 platform SKU mappings, 7 chains) and `docs/sku master.xlsx` (181 SKUs with case size); replaces dummy seed data; idempotent upsert
- Created `scripts/build_combined_mapping.py` — generates `docs/master-data-combined.xlsx`, one file joining platform mappings with SKU master (case size, EAN, HSN); rows missing a SAP code highlighted red (85 rows, 55 of them SWIGGY)
- New validator `CaseSizeRule` (`E008_CASE_SIZE_MISMATCH`) — ordered qty must be a whole multiple of the SKU's case size; message names the SKU, nearest valid quantities, and tells ops to request the platform to reissue the PO
- `SkuMappingRule` (`E002_SKU_UNRESOLVED`) now runs against real data — SKUs in a PO but not in master data are highlighted per platform
- Dockerfile now copies `scripts/` into the image
- Fixed duplicate-email path in `parse_and_persist.py` — rollback was restoring the PARSE_FAIL placeholder PO; cleanup now re-runs after rollback
- Re-validated all SWIGGY POs: 52 case-size violations across 28 POs; 138 unmapped-SKU flags across 22 POs
- Fixed `GET /api/master-data/sku-mappings` 500 — `SkuMappingResponse.notes` was typed `dict` but the DB column is `Text`; corrected to `str` (backend schema + frontend type); endpoint now also filters soft-deleted mappings
- SKU Mappings tab: platform dropdown (all 15 partners) + status dropdown + pagination (50/page) — previously only the first 100 rows (AMAZON + part of BIGBASKET) were visible with no way to page
- Fixed `PATCH /api/master-data/sku-mappings/{id}` — was broken 3 ways: schema required `material_id` UUID while frontend sends `b1_item_code`; `MappingStatus.MANUAL` doesn't exist (→ `MANUALLY_MAPPED`); notes merged as dict onto a Text column. Endpoint now resolves material by `b1_item_code` (case-insensitive)
- Fixed `PATCH /api/pos/{id}` 500 — audit log call passed `changes=` but the `AuditLog` column is `payload`; Edit Purchase Order dialog now saves correctly
- Added `POST /api/pos/{id}/revalidate` + "Re-validate" button on PO detail page — re-runs the validation engine synchronously after ops fixes data (open issues recomputed, resolved ones kept, status → VALIDATED or EXCEPTION); blocked once PO is sent to SAP
- Fixed `GET /api/pos` list 500 — same `issue_date` vs `buyer_po_date` attribute mismatch as the detail endpoint; `POListItem.issue_date` type corrected to `date`
- Fixed PO list pagination — `setParam` deleted the `page` param it had just set, so next/prev buttons never changed page; now only resets page on filter changes
- Fixed PO list status filter — "All statuses" sentinel `__all__` leaked into the API query causing 400; now mapped back to empty before the request
- Fixed Cancel PO — it soft-deleted the row (`deleted_at`), making the PO 404 everywhere; now sets `po_status=CANCELLED` + status history entry, so cancelled POs stay visible (read-only). Restored the one PO cancelled under the old behavior (LKPPO14412)
- Fixed profile menu crash — Base UI requires `DropdownMenuLabel` (a group label) inside `DropdownMenuGroup`; Topbar used it bare, crashing the whole app when the user menu opened
- Material Master tab: added pagination (50/page, ‹ › controls, total count) — previously only the first 50 of 181 items were reachable
- PO Validation tab: resolved issues now render muted with a green "Resolved" badge and strikethrough in a separate section; tab badge counts only OPEN issues (resolved ones previously looked identical to live errors)
- Push to SAP now blocked until all ERROR-severity validation issues are resolved — enforced in the endpoint (400 with count) and in the UI (button disabled with error-count badge + tooltip); WARNING-severity issues do not block. Reset GWAPO38795 stuck in SAP_PENDING from a pre-guard push back to EXCEPTION

## Phase 3 — Swiggy PO Parser + Manual Edit + Push to SAP (2026-07-14)
- Created `app/parsers/swiggy_parser.py` — parses Scootsy/Swiggy SpreadsheetML `.xls` attachments; extracts PO number, dates, ship-to address, 18-column line items (IGST/CGST/SGST/CESS), grand total from footer
- Updated `app/parsers/registry.py` — registered `SwiggyParser` for partner code `SWIGGY`
- Updated `app/workflows/parse_and_persist.py` — added `_cleanup_placeholder_pos()` to delete `PARSE_FAIL_` placeholder POs before re-parsing, preventing duplicates on retry
- Added `POST /api/inbox/messages/{id}/retry-parse` — reset a failed message to PENDING and re-enqueue its parse job
- Added `POST /api/inbox/retry-all-failed?partner_code=SWIGGY` — bulk re-queue all failed parse jobs for a partner
- Added `PATCH /api/pos/{id}` — manual edit of PO header fields (buyer_po_number, dates, GSTIN, ship-to, grand_total)
- Added `POST /api/pos/{id}/push-to-sap` — manual SAP push trigger; moves PO to SAP_PENDING and enqueues sap_push job
- Added `POUpdateRequest` Pydantic schema
- Frontend: Retry Parse button in InboxDetailPage; Retry All Failed button in InboxPage header
- Frontend: Edit PO dialog in PODetailPage (React Hook Form, all header fields editable)
- Frontend: Push to SAP button in PODetailPage (enabled for PARSED/VALIDATED/EXCEPTION statuses)
- Re-queued 44 failed SWIGGY parse jobs; all 44 parsed successfully
- Updated `parse_and_persist.py` — duplicate PO emails (same partner + PO number) now link to the existing PO instead of failing with a unique-constraint error
- Fixed `GET /api/pos/{id}` 500 error — endpoint referenced non-existent model attributes (`source_channel`, `issue_date`, `delivery_date`, `seller_gstin` now mapped from RawMessage/`buyer_po_date`/`requested_delivery_date`/SellerEntity); line items and validation issues built explicitly instead of `model_validate` (field name mismatches: `description`/`uom`/`field_name`/`resolution_note`)

## Phase 2 — Gmail Ingestion + Cloudinary Storage (2026-07-14)
- Added `cloudinary==1.41.0` dependency for PDF/Excel attachment storage
- Added Cloudinary settings to `app/config.py` (`CLOUDINARY_CLOUD_NAME`, `CLOUDINARY_API_KEY`, `CLOUDINARY_API_SECRET`)
- Created `app/adapters/storage.py` — uploads attachments to Cloudinary as `raw` resource type; local disk fallback when credentials absent
- Created `app/adapters/email/swiggy_email.py` — `SwiggyEmailAdapter` targeting `SWIGGY_PO` Gmail label; filters to POs only (rejects GRN, invoice, delivery-note emails by subject keyword)
- Removed `app/adapters/email/blinkit_email.py` — Blinkit uses API/websocket adapter, not email
- Updated `app/workflows/ingest_to_canonical.py`: `_save_attachments()` now uploads to Cloudinary; registry uses `SwiggyEmailAdapter`
- Ran seed script — all 15 trading partners now in DB including SWIGGY (label=`SWIGGY_PO`)
- Scheduler auto-picks up SWIGGY from DB (`gmail_label` is set → polled every 2 min)

## Phase 0 — Foundation Setup (2026-06-29)
- Documented legacy Blinkit/Zepto APIs in `docs/legacy-api-notes.md`
- Documented legacy frontend screens in `docs/legacy-frontend-notes.md`
- Archived `backend/` → `_archive/backend_old/`, `frontend/` → `_archive/frontend_old/`
- Moved `CLAUDE.md` to workspace root
- Repo skeleton with all `__init__.py` stubs
- `pyproject.toml` with all dependencies pinned
- `Dockerfile` (multi-stage Python 3.11-slim)
- `docker-compose.yml` (7 services: postgres, redis, api, scheduler, worker-ingest, worker-parse, worker-sap)
- `.env.example` with all required config vars
- `app/config.py` (pydantic-settings)
- `app/db.py` (async asyncpg + sync psycopg2 engines)
- `app/logging_config.py` (structlog JSON)
- `app/main.py` + `app/api/routes/health.py` (`GET /health`)
- Alembic initialized + migration `0000` (no-op tooling check)
- `.pre-commit-config.yaml` (ruff, mypy, prettier, eslint)
- Frontend: Vite react-ts + Tailwind v4 + shadcn/ui (13 components) + TanStack Query + React Router v6
- Frontend: `api-client.ts`, `queryClient.ts`, `App.tsx`, `router.tsx`, `HomePage` (pings `/health`)
- Frontend: `Dockerfile` (3-stage: dev/builder/production+nginx)
- `README.md` with full setup instructions

## Phase 1 — Canonical EDI Schema (2026-07-02)
- `app/models/_enums.py` — 5 PostgreSQL enums as Python `StrEnum`
- `app/models/master_data.py` — SellerEntity, TradingPartner, MaterialMaster, SkuMapping, ShipToMapping
- `app/models/raw_messages.py` — RawMessage (immutable inbound store)
- `app/models/edi_po.py` — EdiPurchaseOrder, EdiPoLineItem, EdiPoStatusHistory, EdiValidationIssue
- `app/models/asn.py` — EdiAdvanceShipNotice, EdiAsnLineItem
- `app/models/invoice.py` — EdiInvoice, EdiInvoiceLineItem (with IRN/e-way bill fields)
- `app/models/outbound.py` — EdiOutboundMessage
- `app/models/b1_log.py` — B1ApiLog
- `app/schemas/canonical.py` — EDI850, EDI850Line, EDIAddress, ASNDoc, InvoiceDoc, ValidationResult Pydantic schemas
- `alembic/versions/0001_canonical_edi_schema.py` — creates 16 tables, 5 enum types, `updated_at` trigger (applied to 11 tables), `po_status_history` auto-log trigger, views `v_po_summary` + `v_exception_queue`
- `scripts/seed_master_data.py` — seeds 1 seller, 15 partners, 5 materials, 8 SKU mappings, 5 ship-to mappings (idempotent)
- `tests/unit/test_models.py` — 16 unit tests covering save/reload, FKs, unique constraints, soft-delete, enum values
- `docs/erd.png` — ER diagram auto-generated from SQLAlchemy metadata

## Phase 8 — Operations Dashboard (2026-07-06)
- `app/models/users.py` — `User` model (email, password_hash, full_name, is_active)
- `app/models/audit_log.py` — `AuditLog` model (user_email, action, entity_type, entity_id, payload JSONB)
- `alembic/versions/0002_users_and_audit_log.py` — creates `users` + `audit_log` tables with indexes + `trg_users_updated_at` trigger
- `app/schemas/api.py` — `PaginatedResponse[T]` + all API request/response Pydantic schemas (auth, POs, dashboard, exceptions, master data, B1 logs)
- `app/api/routes/auth.py` — `POST /auth/login`, `POST /auth/logout`, `GET /auth/me`; JWT in httpOnly cookie (`edi_token`); HS256; 8h expiry; `get_current_user` FastAPI dependency
- `app/api/routes/pos.py` — `GET /api/pos` (paginated + filtered), `GET /api/pos/{id}`, `POST /api/pos/{id}/retry-sap`, `POST /api/pos/{id}/cancel`
- `app/api/routes/dashboard.py` — `GET /api/dashboard/today`, `/sla-breaches`, `/unmapped-skus`, `/activity`
- `app/api/routes/exceptions.py` — `GET /api/exceptions`, `POST /api/exceptions/{id}/resolve`
- `app/api/routes/master_data.py` — CRUD for partners, materials, SKU mappings, ship-to mappings
- `app/api/routes/b1_logs.py` — `GET /api/b1-logs` (filtered), `GET /api/b1-logs/{id}` (full payload)
- `app/api/middleware.py` — `AuditMiddleware`: logs all POST/PATCH/PUT/DELETE to `/api/` with user email from JWT
- `app/main.py` — wired all Phase 8 routers + AuditMiddleware + SPA static files mount
- `frontend/src/types/index.ts` — full TypeScript interfaces mirroring all Pydantic schemas
- `frontend/src/components/shared/` — `StatusBadge`, `MoneyDisplay`, `DateDisplay`, `EmptyState`, `LoadingSkeleton`
- `frontend/src/components/layout/` — `Shell`, `Sidebar`, `Topbar`
- `frontend/src/features/auth/` — `useAuth` hook (TanStack Query), `ProtectedRoute`
- `frontend/src/pages/LoginPage.tsx` — React Hook Form + Zod login form with error handling
- `frontend/src/features/dashboard/DashboardPage.tsx` — 4 metric cards, per-partner stats, SLA breaches, unmapped SKUs, activity feed; auto-refresh 30s
- `frontend/src/features/pos/POListPage.tsx` — TanStack Table v8; URL-synced filters (search, partner, status, page); pagination
- `frontend/src/features/pos/PODetailPage.tsx` — 6 tabs: Overview, Line Items, Validation Issues, B1 Push History, Outbound Messages, Raw Source; Retry SAP + Cancel PO actions
- `frontend/src/features/exceptions/ExceptionsPage.tsx` — grouped by severity; inline resolve dialog with note field
- `frontend/src/features/master-data/MasterDataPage.tsx` — 4 tabs: Partners, Material Master, SKU Mappings (inline edit), Ship-to (inline edit)
- `frontend/src/features/b1-logs/B1LogsPage.tsx` — filterable table with errors-only toggle; JSON detail dialog
- `frontend/src/router.tsx` — all routes under `ProtectedRoute` + `Shell`; `/login` unprotected
- `frontend/src/hooks/use-toast.ts` — sonner-backed `useToast()` hook
- `pyproject.toml` — added `B008`, `TC002`, `TC003` to ruff ignore list (FastAPI idioms)

## Phase 7 — Outbound Documents (Ack, ASN, Invoice, Credit Note) (2026-07-06)
- `app/adapters/outbound/base.py` — `OutboundResult` dataclass; `BaseOutboundAdapter` ABC with `send()` + `channel` property
- `app/adapters/outbound/blinkit_outbound.py` — `BlinkitOutboundAdapter`: PO_ACK_855 via `acknowledge_po()`, ASN_856 via `send_asn()`; channel = WEBHOOK
- `app/adapters/outbound/zepto_outbound.py` — `ZeptoOutboundAdapter`: ASN_856 via `send_asn()`; channel = API
- `app/adapters/outbound/email_outbound.py` — `EmailOutboundAdapter`: sends MIME multipart emails via Gmail API; requires `gmail.send` scope; supports reply threading via `reply_to_message_id`
- `app/adapters/outbound/registry.py` — `get_outbound_adapter()`: partner-code lookup → channel fallback → `UnsupportedOutboundPartnerError`
- `app/workflows/send_outbound.py` — `send_outbound_message(outbound_msg_id)`: idempotency guard (SENT/FAILED skip); SLA breach log for ACK_855; dispatch via registry; 5-attempt retry schedule [60s, 300s, 1800s, 7200s, 21600s]; sets SENT/PENDING(retry)/FAILED
- `app/workflows/b1_to_outbound.py` — `trigger_acks_for_confirmed_pos()`: creates PO_ACK_855 for SAP_CONFIRMED POs with no existing ACK; `poll_b1_deliveries()`: queries B1 DeliveryNotes linked to Sales Orders, creates EdiASN + ASN_856; `poll_b1_invoices()`: queries B1 Invoices linked to Deliveries, creates EdiInvoice + INVOICE_810; `enqueue_due_retries()`: re-enqueues PENDING messages past next_retry_at; partner-specific payload builders for Blinkit/Zepto/Email
- `app/workflows/rtv_flow.py` — `process_rtv(raw_message_id)`: extracts PO number from email text (4-pattern regex), matches EdiPurchaseOrder, builds B1 Return payload, calls Service Layer `POST /Returns`, creates CREDIT_NOTE EdiOutboundMessage for partner notification
- `app/workers/jobs.py` — `send_outbound_job`, `poll_b1_outbound_job`, `retry_pending_outbound_job`, `process_rtv_job` RQ jobs
- `app/workers/scheduler.py` — added: B1 outbound poll every 5 min, retry pending every 2 min; outbound queue `"outbound"` declared
- `tests/unit/test_outbound.py` — 31 tests: send_outbound_message (8), SLA check (3), registry (5), trigger_acks (3), retry enqueue (2), RTV extraction (5), RTV payload building (3)

## Phase 6 — SAP Business One Service Layer Integration (2026-07-06)
- `app/sap_b1/errors.py` — `B1ApiError` + `B1SessionError` (401) + `B1ClosedPeriodError` (-5002/closed period); parses standard B1 error envelope
- `app/sap_b1/session_pool.py` — thread-safe `SessionPool`; max N concurrent sessions; 29-min TTL; `Condition`-based blocking acquire (30s timeout); auto-purge expired sessions
- `app/sap_b1/client.py` — `ServiceLayerClient`: Login/Logout, `create_sales_order/delivery/invoice/return/credit_note`, `get_item`, `get_business_partner`, `query`; 401 auto-retry with fresh session; module-level singleton via `get_b1_client()`
- `app/mappers/po_to_sales_order.py` — `build_sales_order_payload()`: maps EDI850 → B1 Sales Order JSON; header UDFs (U_EDI_SOURCE/UUID/RECEIVED_AT/BUYER_GSTIN/PO_NUMBER); line UoM conversion via sku_mapping.qty_per_buyer_uom; HSNOrSACCode; line UDFs (U_EDI_LINE_NO/BUYER_SKU)
- `app/workflows/canonical_to_b1.py` — `push_po_to_b1(po_id)`: idempotency guard (b1_sales_order_doc_entry already set → skip); status pre-flight (VALIDATED/SAP_REJECTED only); unmapped-SKU check; SAP_PENDING → call B1 → SAP_CONFIRMED or SAP_REJECTED; always writes B1ApiLog
- `app/workers/jobs.py` — `push_po_to_b1_job(po_id)` RQ job on dedicated `sap_push` queue
- `app/workers/scheduler.py` — added SAP push job: queries VALIDATED POs every 60s, enqueues `push_po_to_b1_job` for each
- `scripts/test_b1_connection.py` — standalone 5-step connectivity verifier (Login, company info, Items read, BusinessPartners read, Logout)
- `docs/b1_setup.md` — full B1 setup guide: Service Layer access, API user permissions, Business Partners, Item master, Warehouses, UDF creation steps (7 UDFs across ORDR/RDR1), tax codes, India localisation checklist
- `tests/unit/test_sap_b1.py` — 34 tests: B1ApiError parsing (6), SessionPool (8), ServiceLayerClient via `responses` mock (8), po_to_sales_order mapper (8), push_po_to_b1 workflow (4)

## Phase 5 — Validation & Master-Data Mapping (2026-07-06)
- `app/validators/engine.py` — `ValidationEngine`, `ValidationContext`, `BaseRule`, `RuleViolation`, `EngineResult` (has_errors / has_warnings)
- `app/validators/rules/gstin.py` — `GstinFormatRule`: missing/malformed buyer GSTIN → ERROR
- `app/validators/rules/sku_mapping.py` — `SkuMappingRule`: auto-maps via exact/cross-partner/fuzzy (rapidfuzz ≥ 0.85); unmapped SKU → ERROR; auto-mapped lines updated with sap_material_no
- `app/validators/rules/ship_to_mapping.py` — `ShipToMappingRule`: unmapped ship-to warehouse → WARNING; propagates b1_whs_code to line items
- `app/validators/rules/tax_consistency.py` — `TaxConsistencyRule`: CGST+IGST both non-zero → ERROR; CGST≠SGST → WARNING
- `app/validators/rules/total_reconciliation.py` — `TotalReconciliationRule`: line sum vs grand_total diff > ₹1 → WARNING
- `app/validators/rules/pricing.py` — `PriceVarianceRule`: unit_price vs contracted_price (from SkuMapping.notes JSON) > threshold% → WARNING
- `app/validators/rules/moq.py` — `MoqRule`: ordered_qty < MOQ (from partner api_config or SkuMapping.notes) → WARNING
- `app/workflows/validate_po.py` — `validate_po(po_id)`: runs engine, persists EdiValidationIssue rows, sets PO status VALIDATED (no errors) or EXCEPTION (any ERROR), writes EdiPoStatusHistory; idempotent re-run
- `app/workers/jobs.py` — `validate_po_job(po_id)` RQ job
- `app/workflows/parse_and_persist.py` — enqueues validate_po_job after successful parse
- `app/api/routes/exceptions.py` — `GET /api/exceptions`, `POST /api/exceptions/{id}/resolve`, `POST /api/sku-mapping`, `GET /api/sku-mapping`
- `app/main.py` — registered exceptions_router
- `tests/unit/test_validators.py` — 38 tests: GstinRule (6), TaxRule (6), TotalRule (6), MoqRule (5), PriceRule (5), ShipToRule (3), SkuRule (3), Engine (4)

## Phase 4 — API-Based Partner Adapters (2026-07-06)
- `app/adapters/api/base.py` — `FetchedPO`, `FetchResult` dataclasses; `BaseApiAdapter` ABC with `fetch_new_pos()` + optional `fetch_po_detail()`
- `app/adapters/api/blinkit_api.py` — outbound-only adapter (Blinkit is webhook-push); `acknowledge_po()` + `send_asn()` with 3-attempt retry, `Retry-After` respect, no retry on 4xx; re-implemented from `_archive/backend_old/app/services/blinkit.py`
- `app/adapters/api/zepto_api.py` — `ZeptoApiAdapter(BaseApiAdapter)` polling Silk Route API; `fetch_new_pos()` with pagination + dedup by `eventId`; `_since_to_days()` watermark → days param (cap 45); `send_asn()`; re-implemented from `_archive/backend_old/app/services/zepto.py`
- `app/api/routes/webhooks.py` — `POST /api/webhooks/{partner_code}` generic dispatcher; Blinkit auth via `api-key` header vs `webhook_secret`; idempotent `_save_raw_message()`; parse enqueue via FastAPI `BackgroundTasks`
- `app/api/deps.py` — `get_sync_db()` FastAPI dependency for sync DB sessions
- `app/workflows/fetch_api_pos.py` — `fetch_and_store_api_pos(partner_code)`: loads partner, reads watermark, calls adapter, saves `RawMessage` rows, enqueues parse jobs, advances watermark on clean run
- `app/workers/jobs.py` — `fetch_api_partner_job(partner_code)` RQ job calling `fetch_and_store_api_pos`
- `app/workers/scheduler.py` — added API polling every 5 min for `source_channel=API` partners alongside existing email ingest
- `app/main.py` — registered `webhooks_router`
- `tests/fixtures/zepto_po_events_response.json` — Zepto paginated events response fixture (2 POs)
- `tests/unit/test_api_adapters.py` — 23 tests: ZeptoApiAdapter (11), BlinkitApiAdapter (7), webhook route (5)

## Phase 3 — Parser Layer (2026-07-06)
- `app/parsers/base.py` — `ParseResult` dataclass; `BaseParser` ABC with `can_parse()` + `parse()`
- `app/parsers/registry.py` — lazy partner-code → parser-class registry; `get_parser()`, `registered_codes()`
- `app/parsers/blinkit_parser.py` — JSON webhook parser; CGST/SGST + IGST branches; header total fallback; re-implemented from `_archive/backend_old/app/routes.py`
- `app/parsers/zepto_parser.py` — JSON API parser (Silk Route v12); nested `productIdentifier` path; re-implemented from `_archive/backend_old/app/services/zepto.py`
- `app/parsers/llm_fallback.py` — Anthropic `claude-sonnet-4-5` fallback; lazy import; only active when `api_config.llm_fallback_enabled=true`
- `app/workflows/parse_and_persist.py` — `parse_and_persist(raw_message_id)`: parser dispatch + LLM fallback + DB write (success → `edi_purchase_orders`+lines; failure → placeholder PO + `E000_PARSE_FAILED` validation issue)
- `app/workers/jobs.py` — `parse_raw_message_job` stub replaced with real implementation calling `parse_and_persist`
- `app/workflows/ingest_to_canonical.py` — `_enqueue_parse_stub` replaced with `_enqueue_parse_job` (RQ enqueue); migrated `session.query()` to SQLAlchemy 2.x `session.execute(select(...))`
- `tests/fixtures/blinkit_po_webhook.json` — 2-line PO with CGST/SGST
- `tests/fixtures/blinkit_po_webhook_igst.json` — 1-line interstate PO with IGST
- `tests/fixtures/zepto_po_event.json` — 2-line Zepto Silk Route API PO
- `tests/unit/test_parsers.py` — 29 tests: BlinkitParser (13), ZeptoParser (12), ParseResult contract (4)

## Phase 2 — Email Ingestion (2026-07-03)
- `app/adapters/email/base.py` — `AttachmentMeta`, `InboundEmail` dataclasses; `BaseEmailAdapter` ABC
- `app/adapters/email/gmail_client.py` — full Gmail API v1 client: OAuth2 token management, label resolution, message listing, recursive MIME part traversal, base64url decoding, attachment download
- `app/adapters/email/blinkit_email.py` — `BlinkitEmailAdapter` (label: `BLINKIT_PO`); accepts @blinkit.com / @grofers.com domains, PO subject keywords, or PDF attachments
- `app/workflows/ingest_to_canonical.py` — `ingest_label()` workflow: Gmail → raw_messages + disk attachments; dual idempotency (pre-check + DB unique constraint); adapter-level `is_po_email` filter; stub parse enqueue for Phase 3
- `app/workers/jobs.py` — `ingest_label_job(partner_code, label_name)` RQ job; `parse_raw_message_job` stub
- `app/workers/scheduler.py` — APScheduler with `BlockingScheduler`; auto-discovers email partners from DB at startup; enqueues ingest jobs via RQ every 2 minutes
- `scripts/auth_gmail.py` — one-time OAuth2 authorization CLI; writes `token.json` to `GMAIL_TOKEN_PATH`
- `tests/unit/conftest.py` — mocks asyncpg/psycopg2 for unit tests (drivers unavailable without Docker)
- `tests/fixtures/gmail_message_po.json` — fixture: multipart/mixed email with PDF attachment
- `tests/fixtures/gmail_message_nonpo.json` — fixture: newsletter email (no attachments)
- `tests/unit/test_gmail_ingestion.py` — 19 tests: MIME parsing (7), BlinkitAdapter filter (7), workflow save/duplicate/filter/error/disk (5)
