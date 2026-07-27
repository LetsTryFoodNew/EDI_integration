import { useState } from "react";

const QUERY_OPTIONS = [
  { opt: "$filter", ex: "GET /Orders?$filter=DocumentStatus eq 'bost_Open' and DocDate ge '2024-01-01'", desc: "Filter records. Operators: eq,ne,gt,lt,ge,le,and,or,not. Functions: substringof, startswith, endswith." },
  { opt: "$select", ex: "GET /PurchaseOrders?$select=DocEntry,DocNum,CardCode,DocTotal", desc: "Return only specified fields. Reduces payload size." },
  { opt: "$top", ex: "GET /Orders?$top=50", desc: "Return first N records. Default page size is 20." },
  { opt: "$skip", ex: "GET /Orders?$skip=20&$top=20", desc: "Skip first N records. Combine with $top for pagination." },
  { opt: "$orderby", ex: "GET /Orders?$orderby=DocDate desc,DocNum asc", desc: "Sort results. Append asc or desc after field name." },
  { opt: "$expand", ex: "GET /Orders(22)?$expand=DocumentLines", desc: "Include related sub-objects inline in the response." },
  { opt: "$count", ex: "GET /Orders/$count", desc: "Return total count of matching records as integer." },
  { opt: "$apply", ex: "GET /Orders?$apply=aggregate(DocTotal with sum as TotalRevenue)", desc: "Aggregation: sum, avg, min, max, count, distinctcount." },
  { opt: "Prefer: odata.maxpagesize", ex: "Header: Prefer: odata.maxpagesize=100", desc: "Override default page size per-request. Set to 0 to disable pagination." },
  { opt: "Prefer: return-no-content", ex: "Header: Prefer: return-no-content", desc: "Returns 204 instead of 201+body on POST. Faster for bulk inserts." },
  { opt: "If-Match (ETag)", ex: "Header: If-Match: W/\"356A192B7913B04C54574D18C28D46E6\"", desc: "Optimistic concurrency for Cancel/Close on ETag-enabled entities. Returns 412 if stale." },
];

const HTTP_CODES = [
  { code: "200 OK", color: "#166534", bg: "#DCFCE7", desc: "Success with body (GET, POST actions like Preview/Login)." },
  { code: "201 Created", color: "#166534", bg: "#DCFCE7", desc: "Entity created. Body contains the full created entity." },
  { code: "204 No Content", color: "#166534", bg: "#DCFCE7", desc: "Success, no body. Returned by PATCH, DELETE, Logout." },
  { code: "400 Bad Request", color: "#991B1B", bg: "#FEE2E2", desc: "Invalid payload, missing required field, or malformed batch." },
  { code: "401 Unauthorized", color: "#991B1B", bg: "#FEE2E2", desc: "Invalid or expired B1SESSION cookie. Re-login required." },
  { code: "404 Not Found", color: "#991B1B", bg: "#FEE2E2", desc: "Entity key not found in SAP database." },
  { code: "412 Precondition Failed", color: "#991B1B", bg: "#FEE2E2", desc: "ETag mismatch - record modified by another user." },
];

const SAP_ERRORS = [
  { code: "-10", desc: "Duplicate key - entity with this code already exists." },
  { code: "-5006", desc: "Operation not supported (e.g. DELETE on Sales Order)." },
  { code: "301", desc: "Invalid or expired session. Re-login and retry." },
  { code: "-2039", desc: "Concurrent modification - ETag mismatch (ODBC -2039)." },
  { code: "-2028", desc: "Resource not found for the requested property." },
  { code: "-1000", desc: "Batch request body format is invalid." },
  { code: "-4002", desc: "Missing required field in the request payload." },
];

const categories = [
  {
    id: "auth", label: "Authentication", icon: "\ud83d\udd10",
    color: "#6D28D9", bg: "#F5F3FF",
    apis: [
      {
        method: "POST", endpoint: "/Login", etag: false,
        use: "Required before any other API call.",
        desc: "Start a session. Returns B1SESSION cookie required on every subsequent request. Default timeout 30 min.",
        headers: ["Content-Type: application/json"],
        payload: "{\n  \"CompanyDB\": \"SBO_COMPANY\",\n  \"UserName\":  \"manager\",\n  \"Password\":  \"yourpassword\"\n}",
        response: "HTTP/1.1 200 OK\nSet-Cookie: B1SESSION=PTRzIjYK-weN6-1Lx1-ZG0J-3ARxfjcU0Shy; HttpOnly\nSet-Cookie: ROUTEID=.node1; path=/b1s\n\n{\n  \"SessionId\":      \"PTRzIjYK-weN6-1Lx1-ZG0J-3ARxfjcU0Shy\",\n  \"Version\":        \"1000110\",\n  \"SessionTimeout\": 30\n}",
        notes: "Store both B1SESSION and ROUTEID cookies. Send both on every subsequent request. Re-login on 401.",
      },
      {
        method: "POST", endpoint: "/Logout", etag: false,
        use: "Call on app shutdown or user sign-out.",
        desc: "End the session and invalidate the B1SESSION cookie.",
        headers: ["Cookie: B1SESSION=<token>; ROUTEID=.node1"],
        payload: null,
        response: "HTTP/1.1 204 No Content",
        notes: null,
      },
      {
        method: "GET", endpoint: "/PingPong", etag: false,
        use: "Health check; session keep-alive.",
        desc: "Health-check endpoint. Confirms Service Layer is reachable and session is still valid. Call every 10-15 min to prevent timeout.",
        headers: ["Cookie: B1SESSION=<token>; ROUTEID=.node1"],
        payload: null,
        response: "HTTP/1.1 200 OK\n{ \"value\": \"Pong\" }",
        notes: null,
      },
    ]
  },
  {
    id: "sales", label: "Sales Documents", icon: "\ud83e\uddfe",
    color: "#0369A1", bg: "#F0F9FF",
    apis: [
      {
        method: "GET / POST / PATCH", endpoint: "/Orders", etag: true,
        use: "Receive platform orders (Zepto/Blinkit) \u2192 create in SAP \u2192 trigger PO.",
        desc: "Sales orders \u2014 primary document in the sales cycle. Supports full CRUD. DocEntry = internal key, DocNum = user-visible number. Cannot be deleted \u2014 use Cancel action.",
        headers: ["Cookie: B1SESSION=<token>; ROUTEID=.node1", "Content-Type: application/json (POST/PATCH)"],
        payload: "// POST \u2014 Create sales order\n{\n  \"CardCode\":    \"C001\",\n  \"DocDate\":     \"2024-01-15\",\n  \"DocDueDate\":  \"2024-01-20\",\n  \"Comments\":    \"Platform Order #BL-9001\",\n  \"DocumentLines\": [\n    {\n      \"ItemCode\":      \"ITEM001\",\n      \"Quantity\":      10,\n      \"UnitPrice\":     250,\n      \"TaxCode\":       \"GST18\",\n      \"WarehouseCode\": \"WH01\"\n    },\n    {\n      \"ItemCode\":      \"ITEM002\",\n      \"Quantity\":      5,\n      \"UnitPrice\":     120,\n      \"TaxCode\":       \"GST18\",\n      \"WarehouseCode\": \"WH01\"\n    }\n  ]\n}",
        response: "HTTP/1.1 201 Created\n{\n  \"DocEntry\":       22,\n  \"DocNum\":         11,\n  \"DocDate\":        \"2024-01-15\",\n  \"CardCode\":       \"C001\",\n  \"DocTotal\":       3260,\n  \"DocumentStatus\": \"bost_Open\",\n  \"DocumentLines\": [\n    { \"LineNum\": 0, \"ItemCode\": \"ITEM001\", \"Quantity\": 10, \"UnitPrice\": 250, \"LineTotal\": 2950 }\n  ]\n}",
        notes: "DocumentStatus: bost_Open | bost_Close. Use $filter=DocumentStatus eq 'bost_Open' to get pending orders.",
        queries: ["GET /Orders?$filter=DocumentStatus eq 'bost_Open'", "GET /Orders?$filter=DocDate ge '2024-01-01'&$select=DocEntry,DocNum,CardCode,DocTotal", "GET /Orders(22)", "GET /Orders?$top=50&$orderby=DocDate desc"],
      },
      {
        method: "POST", endpoint: "/Orders({DocEntry})/Close", etag: true,
        use: "Mark fulfilled orders as closed.",
        desc: "Close a completed sales order. Bound action on Document entity. Use ETag to prevent concurrent edits.",
        headers: ["Cookie: B1SESSION=<token>; ROUTEID=.node1", "If-Match: W/\"<etag>\" (recommended)"],
        payload: null,
        response: "HTTP/1.1 204 No Content",
        notes: "Cannot close an already-closed or cancelled order.",
      },
      {
        method: "POST", endpoint: "/Orders({DocEntry})/Cancel", etag: true,
        use: "Cancel when platform order is cancelled.",
        desc: "Cancel an open sales order. ETag required to prevent concurrent modification conflicts (412 Precondition Failed if stale).",
        headers: ["Cookie: B1SESSION=<token>; ROUTEID=.node1", "If-Match: W/\"<etag>\""],
        payload: null,
        response: "HTTP/1.1 204 No Content\n// Error if another user modified it first:\nHTTP/1.1 412 Precondition Failed\n{ \"error\": { \"code\": \"-2039\", \"message\": { \"value\": \"Another user modified data...\" } } }",
        notes: null,
      },
      {
        method: "POST", endpoint: "/OrdersService_Preview", etag: false,
        use: "Validate order totals and tax before creation.",
        desc: "Preview a sales order \u2014 get calculated totals, tax, and currency WITHOUT saving. DocEntry and DocNum will be null.",
        headers: ["Cookie: B1SESSION=<token>; ROUTEID=.node1", "Content-Type: application/json"],
        payload: "{\n  \"Document\": {\n    \"CardCode\":   \"C001\",\n    \"DocDate\":    \"2024-01-15\",\n    \"DocDueDate\": \"2024-01-20\",\n    \"DocumentLines\": [\n      { \"ItemCode\": \"ITEM001\", \"Quantity\": 10, \"UnitPrice\": 250, \"TaxCode\": \"GST18\" }\n    ]\n  }\n}",
        response: "HTTP/1.1 200 OK\n{\n  \"DocEntry\":       null,\n  \"DocNum\":         null,\n  \"DocTotal\":       2950,\n  \"DocCurrency\":    \"INR\",\n  \"DocumentStatus\": \"bost_Open\",\n  \"DocumentLines\":  [...]\n}",
        notes: "Order is NOT saved. Use to validate before committing.",
      },
      {
        method: "GET / POST / PATCH", endpoint: "/Quotations", etag: true,
        use: "Pre-sales quoting; price negotiation.",
        desc: "Sales quotations sent to customers. Same Document entity structure as Orders. Convert to order using BaseEntry/BaseLine/BaseType=23 in Order's DocumentLines.",
        headers: ["Cookie: B1SESSION=<token>; ROUTEID=.node1", "Content-Type: application/json (POST/PATCH)"],
        payload: "{\n  \"CardCode\":   \"C001\",\n  \"DocDate\":    \"2024-01-10\",\n  \"DocDueDate\": \"2024-01-17\",\n  \"DocumentLines\": [\n    { \"ItemCode\": \"ITEM001\", \"Quantity\": 10, \"UnitPrice\": 250, \"TaxCode\": \"GST18\" }\n  ]\n}",
        response: "HTTP/1.1 201 Created\n{ \"DocEntry\": 5, \"DocNum\": 4, \"DocTotal\": 2950, ... }",
        notes: null,
      },
      {
        method: "GET / POST / PATCH", endpoint: "/DeliveryNotes", etag: true,
        use: "Record goods dispatched to buyer; decrements inventory.",
        desc: "Outbound delivery notes \u2014 records goods dispatched to customer. Decreases stock. Link to sales order via BaseEntry/BaseLine/BaseType=17.",
        headers: ["Cookie: B1SESSION=<token>; ROUTEID=.node1", "Content-Type: application/json (POST/PATCH)"],
        payload: "{\n  \"CardCode\": \"C001\",\n  \"DocDate\":  \"2024-01-18\",\n  \"DocumentLines\": [\n    {\n      \"ItemCode\":  \"ITEM001\",\n      \"Quantity\":  10,\n      \"BaseEntry\": 22,\n      \"BaseLine\":  0,\n      \"BaseType\":  17\n    }\n  ]\n}",
        response: "HTTP/1.1 201 Created\n{ \"DocEntry\": 33, \"DocNum\": 20, ... }",
        notes: "BaseType 17 = Sales Order. BaseEntry = DocEntry of the order. BaseLine = 0-based line index.",
      },
      {
        method: "GET / POST / PATCH", endpoint: "/Returns", etag: true,
        use: "Process customer returns; adds stock back.",
        desc: "Sales return documents \u2014 goods returned from customer back into inventory. BaseType 15 = Delivery Note.",
        headers: ["Cookie: B1SESSION=<token>; ROUTEID=.node1", "Content-Type: application/json (POST/PATCH)"],
        payload: "{\n  \"CardCode\": \"C001\",\n  \"DocDate\":  \"2024-01-22\",\n  \"DocumentLines\": [\n    { \"ItemCode\": \"ITEM001\", \"Quantity\": 2, \"BaseEntry\": 33, \"BaseLine\": 0, \"BaseType\": 15 }\n  ]\n}",
        response: "HTTP/1.1 201 Created\n{ \"DocEntry\": 7, ... }",
        notes: "BaseType 15 = Delivery Note.",
      },
      {
        method: "GET / POST / PATCH", endpoint: "/ReturnRequest", etag: true,
        use: "Approve/reject return requests before processing.",
        desc: "Customer return requests \u2014 pre-authorization before physical return is processed.",
        headers: ["Cookie: B1SESSION=<token>; ROUTEID=.node1", "Content-Type: application/json (POST/PATCH)"],
        payload: "{\n  \"CardCode\": \"C001\",\n  \"DocDate\":  \"2024-01-21\",\n  \"DocumentLines\": [\n    { \"ItemCode\": \"ITEM001\", \"Quantity\": 2 }\n  ]\n}",
        response: "HTTP/1.1 201 Created\n{ \"DocEntry\": 4, ... }",
        notes: null,
      },
      {
        method: "GET / POST / PATCH", endpoint: "/Invoices", etag: true,
        use: "Create AR invoice after delivery for billing.",
        desc: "Accounts Receivable (AR) invoices issued to customers. Link to Delivery Note via BaseEntry/BaseLine/BaseType=15 for 3-way matching.",
        headers: ["Cookie: B1SESSION=<token>; ROUTEID=.node1", "Content-Type: application/json (POST/PATCH)"],
        payload: "{\n  \"CardCode\": \"C001\",\n  \"DocDate\":  \"2024-01-20\",\n  \"DocumentLines\": [\n    {\n      \"ItemCode\":  \"ITEM001\",\n      \"Quantity\":  10,\n      \"UnitPrice\": 250,\n      \"TaxCode\":   \"GST18\",\n      \"BaseEntry\": 33,\n      \"BaseLine\":  0,\n      \"BaseType\":  15\n    }\n  ]\n}",
        response: "HTTP/1.1 201 Created\n{ \"DocEntry\": 45, \"DocNum\": 30, \"DocTotal\": 2950, ... }",
        notes: "BaseType 15 = Delivery Note. Required for 3-way matching.",
      },
      {
        method: "GET / POST / PATCH", endpoint: "/CreditNotes", etag: true,
        use: "Issue refund or billing adjustment to customer.",
        desc: "Credit memos issued to customers \u2014 for returns, adjustments, or billing corrections.",
        headers: ["Cookie: B1SESSION=<token>; ROUTEID=.node1", "Content-Type: application/json (POST/PATCH)"],
        payload: "{\n  \"CardCode\": \"C001\",\n  \"DocDate\":  \"2024-01-23\",\n  \"DocumentLines\": [\n    { \"ItemCode\": \"ITEM001\", \"Quantity\": 2, \"UnitPrice\": 250, \"TaxCode\": \"GST18\" }\n  ]\n}",
        response: "HTTP/1.1 201 Created\n{ \"DocEntry\": 12, ... }",
        notes: null,
      },
      {
        method: "GET / POST / PATCH", endpoint: "/DownPayments", etag: true,
        use: "Advance billing from customer.",
        desc: "Down payment invoices \u2014 collect partial advance payment from customer before delivery.",
        headers: ["Cookie: B1SESSION=<token>; ROUTEID=.node1", "Content-Type: application/json (POST/PATCH)"],
        payload: "{\n  \"CardCode\":    \"C001\",\n  \"DocDate\":     \"2024-01-12\",\n  \"DownPayment\": 5000,\n  \"DocumentLines\": [\n    { \"ItemCode\": \"ITEM001\", \"Quantity\": 1, \"UnitPrice\": 5000 }\n  ]\n}",
        response: "HTTP/1.1 201 Created\n{ \"DocEntry\": 6, ... }",
        notes: null,
      },
      {
        method: "GET / POST / PATCH", endpoint: "/CorrectionInvoice", etag: true,
        use: "Fix customer billing errors post-posting.",
        desc: "Correction to a posted AR invoice \u2014 to amend errors after a invoice has been posted.",
        headers: ["Cookie: B1SESSION=<token>; ROUTEID=.node1", "Content-Type: application/json (POST/PATCH)"],
        payload: "{\n  \"CardCode\": \"C001\",\n  \"DocDate\":  \"2024-01-25\",\n  \"DocumentLines\": [\n    { \"ItemCode\": \"ITEM001\", \"Quantity\": 10, \"UnitPrice\": 260, \"TaxCode\": \"GST18\" }\n  ]\n}",
        response: "HTTP/1.1 201 Created\n{ \"DocEntry\": 50, ... }",
        notes: null,
      },
      {
        method: "GET / POST / PATCH", endpoint: "/CorrectionInvoiceReversal", etag: true,
        use: "Undo a correction invoice.",
        desc: "Full reversal of a correction AR invoice.",
        headers: ["Cookie: B1SESSION=<token>; ROUTEID=.node1", "Content-Type: application/json (POST/PATCH)"],
        payload: "{\n  \"CardCode\": \"C001\",\n  \"DocDate\":  \"2024-01-26\",\n  \"DocumentLines\": [\n    { \"ItemCode\": \"ITEM001\", \"Quantity\": 10, \"UnitPrice\": 260 }\n  ]\n}",
        response: "HTTP/1.1 201 Created\n{ \"DocEntry\": 51, ... }",
        notes: null,
      },
      {
        method: "GET / POST / PATCH", endpoint: "/Drafts", etag: true,
        use: "Save-and-review workflows; approval before posting.",
        desc: "Document drafts \u2014 save any document type as a draft before posting. Use DocObjectCode: 17=Sales Order, 18=AR Invoice, 20=Delivery, 22=Return, 23=PO.",
        headers: ["Cookie: B1SESSION=<token>; ROUTEID=.node1", "Content-Type: application/json (POST/PATCH)"],
        payload: "{\n  \"DocObjectCode\": \"17\",\n  \"CardCode\":      \"C001\",\n  \"DocDate\":       \"2024-01-15\",\n  \"DocumentLines\": [\n    { \"ItemCode\": \"ITEM001\", \"Quantity\": 5, \"UnitPrice\": 250 }\n  ]\n}",
        response: "HTTP/1.1 201 Created\n{ \"DocEntry\": 3, \"DocObjectCode\": \"17\", \"DocumentStatus\": \"bost_Open\", ... }",
        notes: "Draft won't affect stock or accounting until posted. DocObjectCode 17=SO, 22=PO, 46=Purchase Request.",
      },
      {
        method: "GET / POST / PATCH", endpoint: "/SalesOpportunities", etag: false,
        use: "Pre-sales pipeline management and forecasting.",
        desc: "CRM sales pipeline \u2014 track deals, revenue probability, expected close dates.",
        headers: ["Cookie: B1SESSION=<token>; ROUTEID=.node1", "Content-Type: application/json (POST/PATCH)"],
        payload: "{\n  \"CardCode\":       \"C001\",\n  \"SalesPerson\":    5,\n  \"StartDate\":      \"2024-01-10\",\n  \"ClosingDate\":    \"2024-02-28\",\n  \"MaxLocalTotal\":  100000,\n  \"Probability\":    70,\n  \"OpportunityName\":\"Q1 Bulk Order\"\n}",
        response: "HTTP/1.1 201 Created\n{ \"SequenceNo\": 12, \"CardCode\": \"C001\", ... }",
        notes: null,
      },
    ]
  },
  {
    id: "purchase", label: "Purchase Documents", icon: "\ud83d\udce6",
    color: "#065F46", bg: "#ECFDF5",
    apis: [
      {
        method: "GET / POST / PATCH", endpoint: "/PurchaseOrders", etag: true,
        use: "PRIMARY: Auto-create PO after sales order; push to Zepto/Blinkit/Swiggy.",
        desc: "Purchase orders sent to vendors. Core document for your EDI push workflow. Store returned DocEntry to track status.",
        headers: ["Cookie: B1SESSION=<token>; ROUTEID=.node1", "Content-Type: application/json (POST/PATCH)"],
        payload: "// POST \u2014 Create purchase order\n{\n  \"CardCode\":   \"V001\",\n  \"DocDate\":    \"2024-01-15\",\n  \"DocDueDate\": \"2024-01-22\",\n  \"Comments\":   \"Auto-generated from SO#22 / Blinkit BL-9001\",\n  \"DocumentLines\": [\n    {\n      \"ItemCode\":      \"ITEM001\",\n      \"Quantity\":      50,\n      \"UnitPrice\":     200,\n      \"TaxCode\":       \"GST18\",\n      \"WarehouseCode\": \"WH01\"\n    }\n  ]\n}",
        response: "HTTP/1.1 201 Created\n{\n  \"DocEntry\":       11,\n  \"DocNum\":         6,\n  \"CardCode\":       \"V001\",\n  \"DocTotal\":       11800,\n  \"DocumentStatus\": \"bost_Open\",\n  \"DocumentLines\": [\n    { \"LineNum\": 0, \"ItemCode\": \"ITEM001\", \"Quantity\": 50, \"UnitPrice\": 200, \"LineTotal\": 10000 }\n  ]\n}",
        notes: "Required: CardCode (vendor), DocDate, DocDueDate, DocumentLines with ItemCode+Quantity. DocEntry returned \u2014 store it.",
        queries: ["GET /PurchaseOrders?$filter=DocumentStatus eq 'bost_Open'", "GET /PurchaseOrders?$filter=CardCode eq 'V001' and DocDate ge '2024-01-01'", "GET /PurchaseOrders(11)?$select=DocEntry,DocNum,CardCode,DocTotal,DocumentLines", "GET /PurchaseOrders?$orderby=DocDate desc&$top=20"],
      },
      {
        method: "POST", endpoint: "/PurchaseOrders({DocEntry})/Close", etag: true,
        use: "Mark PO complete after goods received.",
        desc: "Close a fulfilled purchase order.",
        headers: ["Cookie: B1SESSION=<token>; ROUTEID=.node1", "If-Match: W/\"<etag>\""],
        payload: null,
        response: "HTTP/1.1 204 No Content",
        notes: null,
      },
      {
        method: "POST", endpoint: "/PurchaseOrders({DocEntry})/Cancel", etag: true,
        use: "Cancel PO when platform order is cancelled.",
        desc: "Cancel an open purchase order. Requires ETag to prevent concurrent modification.",
        headers: ["Cookie: B1SESSION=<token>; ROUTEID=.node1", "If-Match: W/\"<etag>\""],
        payload: null,
        response: "HTTP/1.1 204 No Content",
        notes: null,
      },
      {
        method: "GET / POST / PATCH", endpoint: "/PurchaseRequests", etag: true,
        use: "Internal approval before creating formal PO.",
        desc: "Internal purchase requests \u2014 created before a formal PO is issued. Internal approval workflow step.",
        headers: ["Cookie: B1SESSION=<token>; ROUTEID=.node1", "Content-Type: application/json (POST/PATCH)"],
        payload: "{\n  \"DocDate\":      \"2024-01-14\",\n  \"RequriedDate\": \"2024-01-20\",\n  \"DocumentLines\": [\n    { \"ItemCode\": \"ITEM001\", \"Quantity\": 50 }\n  ]\n}",
        response: "HTTP/1.1 201 Created\n{ \"DocEntry\": 3, \"DocNum\": 2, ... }",
        notes: null,
      },
      {
        method: "GET / POST / PATCH", endpoint: "/PurchaseQuotations", etag: true,
        use: "Get vendor pricing before committing to PO.",
        desc: "Requests for Quotation (RFQ) sent to vendors to get pricing before a formal PO.",
        headers: ["Cookie: B1SESSION=<token>; ROUTEID=.node1", "Content-Type: application/json (POST/PATCH)"],
        payload: "{\n  \"CardCode\":   \"V001\",\n  \"DocDate\":    \"2024-01-10\",\n  \"DocDueDate\": \"2024-01-14\",\n  \"DocumentLines\": [\n    { \"ItemCode\": \"ITEM001\", \"Quantity\": 100 }\n  ]\n}",
        response: "HTTP/1.1 201 Created\n{ \"DocEntry\": 2, ... }",
        notes: null,
      },
      {
        method: "GET / POST / PATCH", endpoint: "/PurchaseDeliveryNotes", etag: true,
        use: "Confirm delivery from vendor; increases inventory.",
        desc: "Goods Receipt PO \u2014 records when ordered goods physically arrive from vendor. Increases stock. BaseType 22 = Purchase Order.",
        headers: ["Cookie: B1SESSION=<token>; ROUTEID=.node1", "Content-Type: application/json (POST/PATCH)"],
        payload: "{\n  \"CardCode\": \"V001\",\n  \"DocDate\":  \"2024-01-22\",\n  \"DocumentLines\": [\n    {\n      \"ItemCode\":  \"ITEM001\",\n      \"Quantity\":  50,\n      \"UnitPrice\": 200,\n      \"BaseEntry\": 11,\n      \"BaseLine\":  0,\n      \"BaseType\":  22\n    }\n  ]\n}",
        response: "HTTP/1.1 201 Created\n{ \"DocEntry\": 15, ... }",
        notes: "BaseType 22 = Purchase Order.",
      },
      {
        method: "GET / POST / PATCH", endpoint: "/PurchaseInvoices", etag: true,
        use: "Record vendor billing; reconcile PO receipt invoice.",
        desc: "AP invoices received from vendors. Link to Goods Receipt via BaseEntry/BaseLine/BaseType=20. Required for 3-way PO matching.",
        headers: ["Cookie: B1SESSION=<token>; ROUTEID=.node1", "Content-Type: application/json (POST/PATCH)"],
        payload: "{\n  \"CardCode\": \"V001\",\n  \"DocDate\":  \"2024-01-23\",\n  \"DocumentLines\": [\n    {\n      \"ItemCode\":  \"ITEM001\",\n      \"Quantity\":  50,\n      \"UnitPrice\": 200,\n      \"TaxCode\":   \"GST18\",\n      \"BaseEntry\": 15,\n      \"BaseLine\":  0,\n      \"BaseType\":  20\n    }\n  ]\n}",
        response: "HTTP/1.1 201 Created\n{ \"DocEntry\": 20, \"DocTotal\": 11800, ... }",
        notes: "BaseType 20 = Goods Receipt PO (PurchaseDeliveryNote).",
      },
      {
        method: "GET / POST / PATCH", endpoint: "/PurchaseCreditNotes", etag: true,
        use: "Vendor credit for damaged or excess goods.",
        desc: "Credit memos received from vendors \u2014 for returned goods or billing corrections.",
        headers: ["Cookie: B1SESSION=<token>; ROUTEID=.node1", "Content-Type: application/json (POST/PATCH)"],
        payload: "{\n  \"CardCode\": \"V001\",\n  \"DocDate\":  \"2024-01-24\",\n  \"DocumentLines\": [\n    { \"ItemCode\": \"ITEM001\", \"Quantity\": 5, \"UnitPrice\": 200, \"TaxCode\": \"GST18\" }\n  ]\n}",
        response: "HTTP/1.1 201 Created\n{ \"DocEntry\": 8, ... }",
        notes: null,
      },
      {
        method: "GET / POST / PATCH", endpoint: "/PurchaseReturns", etag: true,
        use: "Return damaged or incorrect goods to vendor.",
        desc: "Return goods back to vendor \u2014 decreases inventory.",
        headers: ["Cookie: B1SESSION=<token>; ROUTEID=.node1", "Content-Type: application/json (POST/PATCH)"],
        payload: "{\n  \"CardCode\": \"V001\",\n  \"DocDate\":  \"2024-01-24\",\n  \"DocumentLines\": [\n    { \"ItemCode\": \"ITEM001\", \"Quantity\": 5, \"BaseEntry\": 15, \"BaseLine\": 0, \"BaseType\": 20 }\n  ]\n}",
        response: "HTTP/1.1 201 Created\n{ \"DocEntry\": 9, ... }",
        notes: null,
      },
      {
        method: "GET / POST / PATCH", endpoint: "/PurchaseDownPayments", etag: true,
        use: "Pay vendor advance before delivery.",
        desc: "Down payment invoices sent to vendors \u2014 advance payments before delivery.",
        headers: ["Cookie: B1SESSION=<token>; ROUTEID=.node1", "Content-Type: application/json (POST/PATCH)"],
        payload: "{\n  \"CardCode\":    \"V001\",\n  \"DocDate\":     \"2024-01-12\",\n  \"DownPayment\": 5000,\n  \"DocumentLines\": [\n    { \"ItemCode\": \"ITEM001\", \"Quantity\": 1, \"UnitPrice\": 5000 }\n  ]\n}",
        response: "HTTP/1.1 201 Created\n{ \"DocEntry\": 4, ... }",
        notes: null,
      },
      {
        method: "GET / POST / PATCH", endpoint: "/CorrectionPurchaseInvoice", etag: true,
        use: "Fix vendor billing errors post-posting.",
        desc: "Correction to a posted purchase invoice.",
        headers: ["Cookie: B1SESSION=<token>; ROUTEID=.node1", "Content-Type: application/json (POST/PATCH)"],
        payload: "{\n  \"CardCode\": \"V001\",\n  \"DocDate\":  \"2024-01-26\",\n  \"DocumentLines\": [\n    { \"ItemCode\": \"ITEM001\", \"Quantity\": 50, \"UnitPrice\": 210 }\n  ]\n}",
        response: "HTTP/1.1 201 Created\n{ \"DocEntry\": 21, ... }",
        notes: null,
      },
      {
        method: "GET / POST / PATCH", endpoint: "/CorrectionPurchaseInvoiceReversal", etag: true,
        use: "Undo a purchase invoice correction.",
        desc: "Full reversal of a correction purchase invoice.",
        headers: ["Cookie: B1SESSION=<token>; ROUTEID=.node1", "Content-Type: application/json (POST/PATCH)"],
        payload: "{\n  \"CardCode\": \"V001\",\n  \"DocDate\":  \"2024-01-27\",\n  \"DocumentLines\": [\n    { \"ItemCode\": \"ITEM001\", \"Quantity\": 50, \"UnitPrice\": 210 }\n  ]\n}",
        response: "HTTP/1.1 201 Created\n{ \"DocEntry\": 22, ... }",
        notes: null,
      },
    ]
  },
  {
    id: "inventory", label: "Inventory & Items", icon: "\ud83d\udcca",
    color: "#92400E", bg: "#FFFBEB",
    apis: [
      {
        method: "GET / POST / PATCH", endpoint: "/Items", etag: true,
        use: "PRIMARY: Check stock before confirming platform orders.",
        desc: "Item master data \u2014 stock levels, prices, barcodes, UoM, warehouse breakdown. InventoryQuantity = total. ItemWarehouseInfoCollection = per-warehouse breakdown.",
        headers: ["Cookie: B1SESSION=<token>; ROUTEID=.node1", "Content-Type: application/json (POST/PATCH)"],
        payload: "// POST \u2014 create new item\n{\n  \"ItemCode\":        \"ITEM004\",\n  \"ItemName\":        \"New Product 250g\",\n  \"ItemType\":        \"itItems\",\n  \"ItemsGroupCode\":  100,\n  \"PurchaseItem\":    \"tYES\",\n  \"SalesItem\":       \"tYES\",\n  \"InventoryItem\":   \"tYES\",\n  \"BarCode\":         \"8901234567892\",\n  \"ItemPrices\": [\n    { \"PriceList\": 1, \"Price\": 150, \"Currency\": \"INR\" }\n  ]\n}",
        response: "HTTP/1.1 200 OK (GET) / 201 Created (POST)\n{\n  \"ItemCode\":             \"ITEM001\",\n  \"ItemName\":             \"Product A 500ml\",\n  \"InventoryQuantity\":    340,\n  \"OnOrderCount\":         50,\n  \"AvgStdPrice\":          250,\n  \"BarCode\":              \"8901234567890\",\n  \"ItemPrices\": [\n    { \"PriceList\": 1, \"Price\": 299, \"Currency\": \"INR\" }\n  ],\n  \"ItemWarehouseInfoCollection\": [\n    { \"WarehouseCode\": \"WH01\", \"InStock\": 300, \"Committed\": 50, \"OnOrder\": 50 }\n  ]\n}",
        notes: "InventoryQuantity = total stock. InStock in ItemWarehouseInfoCollection = per-warehouse. OnOrderCount = on PO, not yet received.",
        queries: ["GET /Items('ITEM001')?$select=ItemCode,ItemName,InventoryQuantity,ItemPrices", "GET /Items?$filter=InventoryQuantity lt 10", "GET /Items?$filter=ItemsGroupCode eq 100&$select=ItemCode,ItemName,InventoryQuantity"],
      },
      {
        method: "GET / POST / PATCH", endpoint: "/InventoryGenEntries", etag: true,
        use: "Add initial or non-PO stock receipts.",
        desc: "Manual stock-in without a Purchase Order (opening stock, found stock, donation).",
        headers: ["Cookie: B1SESSION=<token>; ROUTEID=.node1", "Content-Type: application/json (POST/PATCH)"],
        payload: "{\n  \"DocDate\":  \"2024-01-15\",\n  \"Comments\": \"Opening stock entry\",\n  \"DocumentLines\": [\n    { \"ItemCode\": \"ITEM001\", \"Quantity\": 100, \"UnitPrice\": 250, \"WarehouseCode\": \"WH01\" }\n  ]\n}",
        response: "HTTP/1.1 201 Created\n{ \"DocEntry\": 5, \"DocTotal\": 25000, ... }",
        notes: null,
      },
      {
        method: "GET / POST / PATCH", endpoint: "/InventoryGenExits", etag: true,
        use: "Write off expired, damaged, or sample stock.",
        desc: "Manual stock-out without a Sales Order (wastage, samples, write-off).",
        headers: ["Cookie: B1SESSION=<token>; ROUTEID=.node1", "Content-Type: application/json (POST/PATCH)"],
        payload: "{\n  \"DocDate\":  \"2024-01-15\",\n  \"Comments\": \"Damaged stock write-off\",\n  \"DocumentLines\": [\n    { \"ItemCode\": \"ITEM001\", \"Quantity\": 5, \"WarehouseCode\": \"WH01\" }\n  ]\n}",
        response: "HTTP/1.1 201 Created\n{ \"DocEntry\": 6, ... }",
        notes: null,
      },
      {
        method: "GET / POST / PATCH", endpoint: "/StockTransfer", etag: false,
        use: "Move stock between dark stores or warehouses.",
        desc: "Transfer stock between warehouses or bin locations.",
        headers: ["Cookie: B1SESSION=<token>; ROUTEID=.node1", "Content-Type: application/json (POST/PATCH)"],
        payload: "{\n  \"DocDate\":       \"2024-01-15\",\n  \"FromWarehouse\": \"WH01\",\n  \"ToWarehouse\":   \"WH02\",\n  \"StockTransferLines\": [\n    {\n      \"ItemCode\":          \"ITEM001\",\n      \"Quantity\":          50,\n      \"FromWarehouseCode\": \"WH01\",\n      \"WarehouseCode\":     \"WH02\"\n    }\n  ]\n}",
        response: "HTTP/1.1 201 Created\n{ \"DocEntry\": 3, ... }",
        notes: null,
      },
      {
        method: "GET / POST / PATCH", endpoint: "/GoodsReturnRequest", etag: true,
        use: "Pre-authorise stock returns before physical receipt.",
        desc: "Request to return goods to inventory from a sales process \u2014 pre-authorization step.",
        headers: ["Cookie: B1SESSION=<token>; ROUTEID=.node1", "Content-Type: application/json (POST/PATCH)"],
        payload: "{\n  \"CardCode\": \"C001\",\n  \"DocDate\":  \"2024-01-21\",\n  \"DocumentLines\": [\n    { \"ItemCode\": \"ITEM001\", \"Quantity\": 3, \"BaseEntry\": 33, \"BaseLine\": 0, \"BaseType\": 15 }\n  ]\n}",
        response: "HTTP/1.1 201 Created\n{ \"DocEntry\": 2, ... }",
        notes: null,
      },
      {
        method: "GET / POST / PATCH", endpoint: "/MaterialRevaluations", etag: false,
        use: "Adjust item cost price after price changes.",
        desc: "Revalue items at a new unit cost \u2014 FIFO/moving average cost adjustment.",
        headers: ["Cookie: B1SESSION=<token>; ROUTEID=.node1", "Content-Type: application/json (POST/PATCH)"],
        payload: "{\n  \"DocDate\": \"2024-01-15\",\n  \"MaterialRevaluationLines\": [\n    { \"ItemCode\": \"ITEM001\", \"Price\": 275 }\n  ]\n}",
        response: "HTTP/1.1 201 Created\n{ \"DocEntry\": 2, ... }",
        notes: null,
      },
      {
        method: "GET / POST / PATCH", endpoint: "/ProductTrees", etag: false,
        use: "Define product kits or manufactured item recipes.",
        desc: "Bill of Materials (BOM) \u2014 defines components that make up a finished/assembled product.",
        headers: ["Cookie: B1SESSION=<token>; ROUTEID=.node1", "Content-Type: application/json (POST/PATCH)"],
        payload: "{\n  \"TreeCode\":  \"KIT001\",\n  \"TreeType\":  \"iProductionTree\",\n  \"Quantity\":  1,\n  \"ProductTreeLines\": [\n    { \"ItemCode\": \"COMP001\", \"Quantity\": 2 },\n    { \"ItemCode\": \"COMP002\", \"Quantity\": 1 }\n  ]\n}",
        response: "HTTP/1.1 201 Created\n{ \"TreeCode\": \"KIT001\", ... }",
        notes: null,
      },
      {
        method: "GET / POST / PATCH", endpoint: "/LandedCosts", etag: false,
        use: "Allocate freight/duty to imported goods for accurate landed cost.",
        desc: "Additional costs (freight, customs, insurance) allocated to purchased items on top of the purchase price.",
        headers: ["Cookie: B1SESSION=<token>; ROUTEID=.node1", "Content-Type: application/json (POST/PATCH)"],
        payload: "{\n  \"VendorCode\":    \"V001\",\n  \"PostingDate\":   \"2024-01-25\",\n  \"LandedCostDocumentLines\": [\n    { \"GoodReceiptNumber\": 15 }\n  ],\n  \"LandedCostAllocationByLines\": [\n    { \"ItemCode\": \"ITEM001\", \"Quantity\": 50, \"Amount\": 500 }\n  ]\n}",
        response: "HTTP/1.1 201 Created\n{ \"DocEntry\": 5, ... }",
        notes: null,
      },
    ]
  },
  {
    id: "bp", label: "Business Partners", icon: "\ud83e\udd1d",
    color: "#1D4ED8", bg: "#EFF6FF",
    apis: [
      {
        method: "GET / POST / PATCH / DELETE", endpoint: "/BusinessPartners", etag: true,
        use: "Vendor master for PO creation; customer master for orders.",
        desc: "Customers, vendors, and leads master data. Key entity for all buy/sell documents. CardType: cCustomer/C, cSupplier/S, cLead/L. FederalTaxID = GSTIN in India.",
        headers: ["Cookie: B1SESSION=<token>; ROUTEID=.node1", "Content-Type: application/json (POST/PATCH)"],
        payload: "// POST \u2014 create vendor\n{\n  \"CardCode\":     \"V002\",\n  \"CardName\":     \"New Supplier Pvt Ltd\",\n  \"CardType\":     \"cSupplier\",\n  \"Currency\":     \"INR\",\n  \"EmailAddress\": \"supplier@example.com\",\n  \"Phone1\":       \"+91-8888888888\",\n  \"FederalTaxID\": \"27ABCDE9876G1Z3\",\n  \"BPAddresses\": [\n    {\n      \"AddressType\": \"bo_BillTo\",\n      \"Street\":      \"123 Main Road\",\n      \"City\":        \"Mumbai\",\n      \"State\":       \"MH\",\n      \"Country\":     \"IN\",\n      \"ZipCode\":     \"400001\"\n    }\n  ]\n}",
        response: "HTTP/1.1 201 Created\n{\n  \"CardCode\":     \"V002\",\n  \"CardName\":     \"New Supplier Pvt Ltd\",\n  \"CardType\":     \"cSupplier\",\n  \"FederalTaxID\": \"27ABCDE9876G1Z3\",\n  ...\n}",
        notes: "CardType: cCustomer (or C), cSupplier (or S), cLead (or L). DELETE supported only if no transactions exist.",
        queries: ["GET /BusinessPartners?$filter=CardType eq 'cSupplier'", "GET /BusinessPartners?$filter=CardType eq 'cCustomer'&$select=CardCode,CardName,EmailAddress", "GET /BusinessPartners('V001')"],
      },
      {
        method: "GET / POST / PATCH", endpoint: "/Activities", etag: true,
        use: "CRM activity tracking and follow-ups.",
        desc: "CRM activities \u2014 calls, meetings, tasks, emails linked to business partners or documents.",
        headers: ["Cookie: B1SESSION=<token>; ROUTEID=.node1", "Content-Type: application/json (POST/PATCH)"],
        payload: "{\n  \"CardCode\":     \"C001\",\n  \"ActivityDate\": \"2024-01-15\",\n  \"ActivityType\": \"cn_Conversation\",\n  \"Details\":      \"Follow-up call about delivery\",\n  \"Priority\":     \"pr_Normal\"\n}",
        response: "HTTP/1.1 201 Created\n{ \"ActivityCode\": 55, \"CardCode\": \"C001\", ... }",
        notes: "Legacy global actions: POST /ActivitiesService_AddActivity and POST /ActivitiesService_GetActivity also work.",
      },
      {
        method: "POST", endpoint: "/ActivitiesService_AddActivity", etag: false,
        use: "Add CRM activity via legacy global action.",
        desc: "Global action to add an activity (legacy method, still works pre-9.1 PL02 style).",
        headers: ["Cookie: B1SESSION=<token>; ROUTEID=.node1", "Content-Type: application/json"],
        payload: "{\n  \"Activity\": {\n    \"ActivityCode\": 1,\n    \"CardCode\":     \"C001\",\n    \"Notes\":        \"Call scheduled\"\n  }\n}",
        response: "HTTP/1.1 200 OK\n{ \"ActivityCode\": 56 }",
        notes: null,
      },
      {
        method: "GET / POST / PATCH", endpoint: "/ServiceCalls", etag: false,
        use: "Log and track customer complaints or service requests.",
        desc: "Customer service calls and after-sales support tickets.",
        headers: ["Cookie: B1SESSION=<token>; ROUTEID=.node1", "Content-Type: application/json (POST/PATCH)"],
        payload: "{\n  \"CustomerCode\":   \"C001\",\n  \"Subject\":        \"Damaged delivery\",\n  \"CallType\":       1,\n  \"TechnicianCode\": 5,\n  \"ItemCode\":       \"ITEM001\",\n  \"Priority\":       \"pr_High\"\n}",
        response: "HTTP/1.1 201 Created\n{ \"ServiceCallID\": 30, \"CustomerCode\": \"C001\", ... }",
        notes: null,
      },
    ]
  },
  {
    id: "finance", label: "Finance & Payments", icon: "\ud83d\udcb0",
    color: "#9D174D", bg: "#FFF1F2",
    apis: [
      {
        method: "GET / POST", endpoint: "/JournalEntries", etag: false,
        use: "Manual GL entries for adjustments.",
        desc: "General ledger journal entries \u2014 manual accounting entries across GL accounts.",
        headers: ["Cookie: B1SESSION=<token>; ROUTEID=.node1", "Content-Type: application/json (POST)"],
        payload: "{\n  \"ReferenceDate\": \"2024-01-15\",\n  \"Memo\":          \"Manual adjustment - freight\",\n  \"JournalEntryLines\": [\n    { \"AccountCode\": \"120001\", \"Debit\": 5000, \"Credit\": 0 },\n    { \"AccountCode\": \"200001\", \"Debit\": 0,    \"Credit\": 5000 }\n  ]\n}",
        response: "HTTP/1.1 201 Created\n{ \"JdtNum\": 101, \"ReferenceDate\": \"2024-01-15\", \"TransactionCode\": \"JE\", ... }",
        notes: null,
      },
      {
        method: "GET / POST / PATCH", endpoint: "/Payments", etag: false,
        use: "Record payment against vendor or customer invoice.",
        desc: "Outgoing or incoming payments \u2014 bank transfer, cash, cheque, credit card. PaymentType: bop_Outgoing (pay vendor), bop_Incoming (receive from customer).",
        headers: ["Cookie: B1SESSION=<token>; ROUTEID=.node1", "Content-Type: application/json (POST/PATCH)"],
        payload: "{\n  \"CardCode\":        \"V001\",\n  \"DocDate\":         \"2024-01-20\",\n  \"PaymentType\":     \"bop_Outgoing\",\n  \"TransferAccount\": \"111100\",\n  \"TransferDate\":    \"2024-01-20\",\n  \"TransferSum\":     11800,\n  \"PaymentInvoices\": [\n    {\n      \"DocEntry\":    20,\n      \"DocLine\":     0,\n      \"InvoiceType\": \"it_PurchaseInvoice\",\n      \"SumApplied\":  11800\n    }\n  ]\n}",
        response: "HTTP/1.1 201 Created\n{ \"DocEntry\": 30, \"DocNum\": 25, \"DocTotal\": 11800, ... }",
        notes: null,
      },
      {
        method: "GET", endpoint: "/ChartOfAccounts", etag: false,
        use: "Reference account codes for journal entries and payments.",
        desc: "GL account master \u2014 account codes, names, types, currency.",
        headers: ["Cookie: B1SESSION=<token>; ROUTEID=.node1"],
        payload: null,
        response: "HTTP/1.1 200 OK\n{\n  \"value\": [\n    { \"Code\": \"120001\", \"Name\": \"Trade Receivables\", \"AccountType\": \"at_Assets\" },\n    { \"Code\": \"200001\", \"Name\": \"Trade Payables\",    \"AccountType\": \"at_Liabilities\" }\n  ]\n}",
        notes: null,
        queries: ["GET /ChartOfAccounts?$filter=AccountType eq 'at_Assets'", "GET /ChartOfAccounts?$select=Code,Name,AccountType"],
      },
      {
        method: "GET / POST / PATCH", endpoint: "/AdditionalExpenses", etag: true,
        use: "Define freight/insurance charge types for documents.",
        desc: "Additional expense type definitions (freight, insurance, handling) that can be added to marketing documents.",
        headers: ["Cookie: B1SESSION=<token>; ROUTEID=.node1", "Content-Type: application/json (POST/PATCH)"],
        payload: "{\n  \"ExpenseName\":         \"Freight\",\n  \"FixedAmountRevenues\": 200,\n  \"TaxCode\":             \"GST18\"\n}",
        response: "HTTP/1.1 201 Created\n{ \"ExpenseCode\": 5, \"ExpenseName\": \"Freight\", ... }",
        notes: null,
      },
    ]
  },
  {
    id: "production", label: "Production & WMS", icon: "\ud83c\udfed",
    color: "#5B21B6", bg: "#F5F3FF",
    apis: [
      {
        method: "GET / POST / PATCH", endpoint: "/ProductionOrders", etag: false,
        use: "Trigger production runs for assembled products.",
        desc: "Manufacturing/production orders to produce finished goods from BOM components.",
        headers: ["Cookie: B1SESSION=<token>; ROUTEID=.node1", "Content-Type: application/json (POST/PATCH)"],
        payload: "{\n  \"ItemNo\":             \"KIT001\",\n  \"PlannedQuantity\":    100,\n  \"ProductionOrderType\": \"bopotStandard\",\n  \"DueDate\":            \"2024-01-30\",\n  \"WarehouseCode\":      \"WH01\",\n  \"ProductionOrderLines\": [\n    { \"ItemNo\": \"COMP001\", \"PlannedQuantity\": 200 },\n    { \"ItemNo\": \"COMP002\", \"PlannedQuantity\": 100 }\n  ]\n}",
        response: "HTTP/1.1 201 Created\n{ \"AbsoluteEntry\": 12, \"ItemNo\": \"KIT001\", \"PlannedQuantity\": 100, ... }",
        notes: null,
      },
      {
        method: "GET / POST / PATCH", endpoint: "/PickLists", etag: false,
        use: "Generate pick lists for warehouse staff.",
        desc: "Warehouse pick lists generated against sales orders for fulfilment.",
        headers: ["Cookie: B1SESSION=<token>; ROUTEID=.node1", "Content-Type: application/json (POST/PATCH)"],
        payload: "{\n  \"ObjectType\":     \"17\",\n  \"PickListsLines\": [\n    { \"OrderEntry\": 22, \"OrderRowID\": 0 }\n  ]\n}",
        response: "HTTP/1.1 201 Created\n{ \"Absoluteentry\": 5, ... }",
        notes: null,
      },
      {
        method: "GET / POST", endpoint: "/Resources", etag: false,
        use: "Define production capacity resources.",
        desc: "Production resources \u2014 machines and labour used in production orders.",
        headers: ["Cookie: B1SESSION=<token>; ROUTEID=.node1", "Content-Type: application/json (POST)"],
        payload: "{\n  \"Code\":          \"MACHINE01\",\n  \"Name\":          \"Packaging Machine\",\n  \"ResourceType\":  \"rtMachine\",\n  \"WarehouseCode\": \"WH01\"\n}",
        response: "HTTP/1.1 201 Created\n{ \"Code\": \"MACHINE01\", ... }",
        notes: null,
      },
    ]
  },
  {
    id: "master", label: "Master Data & Setup", icon: "\u2699\ufe0f",
    color: "#374151", bg: "#F9FAFB",
    apis: [
      {
        method: "GET / POST / PATCH", endpoint: "/Warehouses", etag: false,
        use: "Add new warehouses or dark store locations.",
        desc: "Warehouse master \u2014 codes, names, addresses.",
        headers: ["Cookie: B1SESSION=<token>; ROUTEID=.node1", "Content-Type: application/json (POST/PATCH)"],
        payload: "{\n  \"WarehouseCode\": \"WH03\",\n  \"WarehouseName\": \"Dark Store Bangalore\",\n  \"Street\":        \"100 Ring Road\",\n  \"City\":          \"Bangalore\",\n  \"Country\":       \"IN\",\n  \"State\":         \"KA\"\n}",
        response: "HTTP/1.1 201 Created\n{ \"WarehouseCode\": \"WH03\", ... }",
        notes: null,
      },
      {
        method: "GET / POST / PATCH", endpoint: "/PriceLists", etag: false,
        use: "Define pricing tiers for customers/vendors.",
        desc: "Price list master definitions. Item prices are set on /Items under ItemPrices[].",
        headers: ["Cookie: B1SESSION=<token>; ROUTEID=.node1", "Content-Type: application/json (POST/PATCH)"],
        payload: "{\n  \"PriceListName\": \"Distributor Price\",\n  \"IsGrossPrice\":  \"tNO\",\n  \"Currency\":      \"INR\"\n}",
        response: "HTTP/1.1 201 Created\n{ \"PriceListNo\": 3, \"PriceListName\": \"Distributor Price\", ... }",
        notes: null,
      },
      {
        method: "GET / POST / PATCH", endpoint: "/SpecialPrices", etag: false,
        use: "Customer-specific pricing agreements.",
        desc: "Special prices for specific business partners, overriding the default price list.",
        headers: ["Cookie: B1SESSION=<token>; ROUTEID=.node1", "Content-Type: application/json (POST/PATCH)"],
        payload: "{\n  \"CardCode\":    \"C001\",\n  \"PriceListNo\": 1,\n  \"SpecialPriceDataAreas\": [\n    { \"ItemCode\": \"ITEM001\", \"Price\": 230, \"Currency\": \"INR\" }\n  ]\n}",
        response: "HTTP/1.1 201 Created\n{ ... }",
        notes: null,
      },
      {
        method: "GET / POST", endpoint: "/VatGroups", etag: false,
        use: "Reference valid TaxCode values for document lines.",
        desc: "VAT/tax code groups \u2014 GST codes used on document lines.",
        headers: ["Cookie: B1SESSION=<token>; ROUTEID=.node1"],
        payload: "{\n  \"Code\":      \"GST18\",\n  \"Name\":      \"GST 18%\",\n  \"VatType\":   \"vt_OutputTax\",\n  \"VatGroups_Lines\": [\n    { \"EffectiveDate\": \"2024-01-01\", \"Rate\": 18 }\n  ]\n}",
        response: "HTTP/1.1 201 Created\n{ \"Code\": \"GST18\", \"Name\": \"GST 18%\", \"Rate\": 18, ... }",
        notes: null,
        queries: ["GET /VatGroups?$filter=VatType eq 'vt_OutputTax'", "GET /VatGroups('GST18')"],
      },
      {
        method: "GET / POST", endpoint: "/PaymentTermsTypes", etag: false,
        use: "Assign payment terms on BPs or documents.",
        desc: "Payment terms \u2014 Net 30, COD, Immediate, etc.",
        headers: ["Cookie: B1SESSION=<token>; ROUTEID=.node1"],
        payload: "{\n  \"PaymentTermsGroupName\":      \"Net 30\",\n  \"NumberOfAdditionalMonths\":   1,\n  \"NumberOfAdditionalDays\":     0\n}",
        response: "HTTP/1.1 201 Created\n{ \"GroupNumber\": 2, \"PaymentTermsGroupName\": \"Net 30\", ... }",
        notes: null,
      },
      {
        method: "GET", endpoint: "/Currencies", etag: false,
        use: "Multi-currency transaction support.",
        desc: "Currency master \u2014 codes, names, decimal places.",
        headers: ["Cookie: B1SESSION=<token>; ROUTEID=.node1"],
        payload: null,
        response: "HTTP/1.1 200 OK\n{ \"value\": [{ \"Code\": \"INR\", \"Name\": \"Indian Rupee\", \"DocumentsDecimalPlaces\": 2 }] }",
        notes: null,
      },
      {
        method: "GET", endpoint: "/Countries", etag: false,
        use: "Address validation and tax compliance reference.",
        desc: "Country master data \u2014 codes, names.",
        headers: ["Cookie: B1SESSION=<token>; ROUTEID=.node1"],
        payload: null,
        response: "HTTP/1.1 200 OK\n{ \"value\": [{ \"Code\": \"IN\", \"Name\": \"India\" }] }",
        notes: null,
      },
      {
        method: "GET", endpoint: "/Banks", etag: false,
        use: "Bank master reference for payment setup.",
        desc: "Bank master data \u2014 bank codes, names, branches.",
        headers: ["Cookie: B1SESSION=<token>; ROUTEID=.node1"],
        payload: null,
        response: "HTTP/1.1 200 OK\n{ \"value\": [{ \"BankCode\": \"HDFC\", \"BankName\": \"HDFC Bank\", \"Country\": \"IN\" }] }",
        notes: null,
      },
      {
        method: "GET / POST", endpoint: "/SalesTaxAuthorities", etag: false,
        use: "Reference tax jurisdictions for multi-state compliance.",
        desc: "Sales tax authority master \u2014 multiple keys (Code + Type). Used for US state tax.",
        headers: ["Cookie: B1SESSION=<token>; ROUTEID=.node1"],
        payload: null,
        response: "HTTP/1.1 200 OK\n{ \"value\": [{ \"Code\": \"AK\", \"Type\": -3, \"Name\": \"Alaska Tax\" }] }",
        notes: null,
        queries: ["GET /SalesTaxAuthorities(Code='AK',Type=-3)"],
      },
      {
        method: "GET / POST", endpoint: "/BusinessPlaces", etag: false,
        use: "Multi-branch GST (GSTIN per state) in India.",
        desc: "Business place / branch \u2014 required for GST compliance in India with multi-GSTIN support.",
        headers: ["Cookie: B1SESSION=<token>; ROUTEID=.node1", "Content-Type: application/json (POST)"],
        payload: "{\n  \"Name\":       \"Mumbai Branch\",\n  \"TaxpayerID\": \"27ABCDE1234F1Z5\",\n  \"State\":      \"MH\"\n}",
        response: "HTTP/1.1 201 Created\n{ \"BPLId\": 2, \"Name\": \"Mumbai Branch\", ... }",
        notes: null,
      },
    ]
  },
  {
    id: "udf", label: "User-Defined (UDF / UDT / UDO)", icon: "\ud83d\udee0\ufe0f",
    color: "#0F766E", bg: "#F0FDFA",
    apis: [
      {
        method: "GET / POST / PATCH / DELETE", endpoint: "/UserFieldsMD", etag: false,
        use: "Add Zepto/Blinkit order ID field to sales/purchase orders.",
        desc: "Create and manage custom fields (UDFs) on any SAP table. Field accessed as U_FieldName on documents. Restart Service Layer after creation.",
        headers: ["Cookie: B1SESSION=<token>; ROUTEID=.node1", "Content-Type: application/json (POST/PATCH)"],
        payload: "// POST \u2014 add custom field to Sales Order table (ORDR)\n{\n  \"Name\":        \"PlatformOrderID\",\n  \"Type\":        \"db_Alpha\",\n  \"Size\":        50,\n  \"Description\": \"External platform order ID (Zepto/Blinkit)\",\n  \"TableName\":   \"ORDR\"\n}",
        response: "{\n  \"Name\":      \"PlatformOrderID\",\n  \"Type\":      \"db_Alpha\",\n  \"TableName\": \"ORDR\",\n  \"FieldID\":   0,\n  \"EditSize\":  50,\n  \"Mandatory\": \"tNO\"\n}",
        notes: "SAP table codes: ORDR=Sales Orders, OPOR=PO, OPCH=PurchaseInvoice, OINV=AR Invoice, OCRD=BusinessPartner, OITM=Items. Access as U_PlatformOrderID on documents.",
        queries: ["GET /UserFieldsMD?$filter=TableName eq 'ORDR'", "GET /UserFieldsMD(TableName='ORDR', FieldID=0)", "GET /UserFieldsMD?$filter=Name eq 'PlatformOrderID'"],
      },
      {
        method: "GET / POST / PATCH / DELETE", endpoint: "/UserTablesMD", etag: false,
        use: "Create sync log tables for EDI platform mapping.",
        desc: "Create custom tables (UDTs) in the SAP database. TableType: bott_NoObject=plain, bott_Document=has header+lines, bott_MasterData=master.",
        headers: ["Cookie: B1SESSION=<token>; ROUTEID=.node1", "Content-Type: application/json (POST/PATCH)"],
        payload: "{\n  \"TableName\":        \"PlatformSync\",\n  \"TableDescription\": \"Platform order sync log\",\n  \"TableType\":        \"bott_NoObject\"\n}",
        response: "{ \"TableName\": \"PlatformSync\", \"TableType\": \"bott_NoObject\" }",
        notes: "Restart Service Layer after creating UDT.",
      },
      {
        method: "GET / POST / PATCH / DELETE", endpoint: "/UserObjectsMD", etag: false,
        use: "Create fully custom business objects with SAP-like lifecycle.",
        desc: "Register User-Defined Objects (UDOs) \u2014 custom business objects with full CRUD + Close/Cancel lifecycle. After registration, accessible as /{Name}.",
        headers: ["Cookie: B1SESSION=<token>; ROUTEID=.node1", "Content-Type: application/json (POST/PATCH)"],
        payload: "{\n  \"Code\":       \"MYORDER\",\n  \"Name\":       \"MyOrder\",\n  \"ObjectType\": \"bott_Document\",\n  \"TableName\":  \"MYORDER\",\n  \"CanFind\":    \"tYES\",\n  \"CanClose\":   \"tYES\",\n  \"CanCancel\":  \"tYES\",\n  \"ChildTables\": [\n    { \"SonNumber\": 1, \"TableName\": \"MYORDER1\" }\n  ]\n}",
        response: "{ \"Code\": \"MYORDER\", \"Name\": \"MyOrder\", ... }",
        notes: "After registration, use POST/GET/PATCH/DELETE /MyOrder. Supports Close/Cancel actions.",
      },
      {
        method: "GET / POST / PATCH / DELETE", endpoint: "/UserKeysMD", etag: false,
        use: "Enforce uniqueness on custom table fields.",
        desc: "Manage unique key constraints on user-defined tables.",
        headers: ["Cookie: B1SESSION=<token>; ROUTEID=.node1", "Content-Type: application/json (POST/PATCH)"],
        payload: "{\n  \"TableName\": \"PlatformSync\",\n  \"KeyIndex\":   1,\n  \"Unique\":     \"tYES\",\n  \"KeyFields\": [\n    { \"FieldAlias\": \"PlatformOrderID\" }\n  ]\n}",
        response: "{ \"TableName\": \"PlatformSync\", \"KeyIndex\": 1, ... }",
        notes: null,
      },
      {
        method: "GET / POST / PATCH / DELETE", endpoint: "/U_{TableName}", etag: false,
        use: "Store EDI sync log \u2014 track which platform orders were pushed to SAP.",
        desc: "CRUD on records in any user-defined table. Replace {TableName} with your actual UDT name (e.g. /U_PlatformSync).",
        headers: ["Cookie: B1SESSION=<token>; ROUTEID=.node1", "Content-Type: application/json (POST/PATCH)"],
        payload: "// POST to /U_PlatformSync\n{\n  \"Name\":               \"SYNC-001\",\n  \"U_PlatformOrderID\":  \"BL-9001\",\n  \"U_SAPDocEntry\":      \"22\",\n  \"U_SyncStatus\":       \"Pushed\",\n  \"U_SyncTime\":         \"2024-01-15T10:30:00\"\n}",
        response: "{ \"Code\": \"SYNC-001\", \"Name\": \"SYNC-001\", ... }",
        notes: null,
      },
    ]
  },
  {
    id: "sql", label: "SQL & Semantic Layer", icon: "\ud83d\udd0e",
    color: "#B45309", bg: "#FEFCE8",
    apis: [
      {
        method: "GET / POST / PATCH / DELETE", endpoint: "/SQLQueries", etag: false,
        use: "Pre-defined complex queries for reporting dashboards.",
        desc: "Store named SQL SELECT queries in SAP. Execute stored query via /SQLQueries(code)/List. Only SELECT allowed.",
        headers: ["Cookie: B1SESSION=<token>; ROUTEID=.node1", "Content-Type: application/json (POST/PATCH)"],
        payload: "{\n  \"SqlCode\": \"low_stock\",\n  \"SqlName\": \"Low Stock Report\",\n  \"SqlText\": \"SELECT ItemCode, ItemName, OnHand FROM OITM WHERE OnHand < 10\"\n}",
        response: "// Create: HTTP/1.1 201 Created\n{ \"SqlCode\": \"low_stock\", ... }\n\n// Execute: POST /SQLQueries('low_stock')/List\nHTTP/1.1 200 OK\n{ \"value\": [{ \"ItemCode\": \"ITEM001\", \"ItemName\": \"Product A\", \"OnHand\": 5 }] }",
        notes: "Only SELECT queries allowed (security). Configure table/column allowlist in b1s.conf.",
        queries: ["POST /SQLQueries('low_stock')/List", "GET /SQLQueries?$filter=SqlCode eq 'low_stock'"],
      },
      {
        method: "GET", endpoint: "/b1s/v1/sml.svc/{ViewName}", etag: false,
        use: "Analytics queries \u2014 sales trends, purchase analytics.",
        desc: "Semantic Layer HANA views \u2014 pre-built analytical calculation views exposed as OData entities. HANA version only.",
        headers: ["Cookie: B1SESSION=<token>; ROUTEID=.node1"],
        payload: null,
        response: "HTTP/1.1 200 OK\n{\n  \"value\": [\n    {\n      \"PostingYear\":         \"2024\",\n      \"BusinessPartnerCode\": \"V001\",\n      \"PurchaseAmountLC\":    500000,\n      \"AverageUnitPriceLC\":  200,\n      \"id__\":                1\n    }\n  ]\n}",
        notes: "Virtual key id__ added for OData compliance. GET /b1s/v1/sml.svc/ to list all available views.",
        queries: ["GET /b1s/v1/sml.svc/AveragePurchasingPriceQuery", "GET /b1s/v1/sml.svc/AveragePurchasingPriceQuery?$filter=PostingYear eq '2024'&$top=50", "GET /b1s/v1/sml.svc/SalesOrderDetailQuery?$orderby=DocumentDate desc"],
      },
    ]
  },
  {
    id: "utility", label: "Batch & Utility", icon: "\u26a1",
    color: "#6B21A8", bg: "#FAF5FF",
    apis: [
      {
        method: "POST", endpoint: "/$batch", etag: false,
        use: "Fetch stock + orders + create PO in one HTTP call. Critical for performance.",
        desc: "Send multiple GET/POST/PATCH in a single HTTP call. Atomic change sets roll back together on failure. GET not allowed inside changesets.",
        headers: ["Cookie: B1SESSION=<token>; ROUTEID=.node1", "Content-Type: multipart/mixed;boundary=batch_<uuid>", "OData-Version: 4.0"],
        payload: "POST /b1s/v2/$batch\nContent-Type: multipart/mixed;boundary=batch_abc123\nOData-Version: 4.0\n\n--batch_abc123\nContent-Type: application/http\nContent-Transfer-Encoding: binary\n\nGET /b1s/v2/Items('ITEM001')\n\n\n--batch_abc123\nContent-Type: multipart/mixed;boundary=changeset_xyz789\n\n--changeset_xyz789\nContent-Type: application/http\nContent-Transfer-Encoding: binary\nContent-ID: 1\n\nPOST /b1s/v2/PurchaseOrders\nContent-Type: application/json\n\n{\"CardCode\":\"V001\",\"DocDate\":\"2024-01-15\",\"DocDueDate\":\"2024-01-22\",\"DocumentLines\":[{\"ItemCode\":\"ITEM001\",\"Quantity\":50,\"UnitPrice\":200}]}\n\n--changeset_xyz789--\n--batch_abc123--",
        response: "HTTP/1.1 200 OK\n\n--batchresponse_<uuid>\nHTTP/1.1 200 OK\n{ \"ItemCode\": \"ITEM001\", \"InventoryQuantity\": 340 }\n\n--batchresponse_<uuid>\n--changesetresponse_<uuid>\nContent-ID: 1\nHTTP/1.1 201 Created\n{ \"DocEntry\": 11, \"DocNum\": 6, ... }\n--changesetresponse_<uuid>--\n--batchresponse_<uuid>--",
        notes: "Changeset = atomic (all or nothing rollback). GET not allowed in changeset. Content-ID mandatory in OData v4 changesets.",
      },
      {
        method: "GET", endpoint: "/ (Service Document)", etag: false,
        use: "Discover all available API endpoints on your SAP instance.",
        desc: "Returns JSON list of all exposed entity sets and URLs. Call GET / on the root URL.",
        headers: ["Cookie: B1SESSION=<token>; ROUTEID=.node1"],
        payload: null,
        response: "HTTP/1.1 200 OK\n{\n  \"value\": [\n    { \"name\": \"Orders\",         \"kind\": \"EntitySet\", \"url\": \"Orders\" },\n    { \"name\": \"PurchaseOrders\", \"kind\": \"EntitySet\", \"url\": \"PurchaseOrders\" },\n    { \"name\": \"Items\",          \"kind\": \"EntitySet\", \"url\": \"Items\" },\n    ...\n  ]\n}",
        notes: null,
      },
      {
        method: "GET", endpoint: "/$metadata", etag: false,
        use: "Discover all field names and types. Generate typed client code.",
        desc: "OData CSDL XML document \u2014 all entity types, property names, data types, keys, relationships.",
        headers: ["Cookie: B1SESSION=<token>; ROUTEID=.node1"],
        payload: null,
        response: "HTTP/1.1 200 OK\n\n<?xml version=\"1.0\"?>\n<edmx:Edmx Version=\"4.0\">\n  <Schema Namespace=\"SAPB1\">\n    <EntityType Name=\"Document\">\n      <Key><PropertyRef Name=\"DocEntry\"/></Key>\n      <Property Name=\"DocEntry\"  Type=\"Edm.Int32\"/>\n      <Property Name=\"CardCode\"  Type=\"Edm.String\"/>\n      <Property Name=\"DocTotal\"  Type=\"Edm.Double\"/>\n      ...\n    </EntityType>\n    <EntitySet Name=\"Orders\"         EntityType=\"SAPB1.Document\"/>\n    <EntitySet Name=\"PurchaseOrders\" EntityType=\"SAPB1.Document\"/>\n    ...\n  </Schema>\n</edmx:Edmx>",
        notes: null,
      },
      {
        method: "GET / POST / PATCH", endpoint: "/Attachments2", etag: false,
        use: "Attach vendor invoices, delivery docs, or images to SAP records.",
        desc: "Upload (JSON path or multipart form), download, and update file attachments. Supported: pdf,doc,docx,jpg,jpeg,png,txt,xls,ppt.",
        headers: ["Cookie: B1SESSION=<token>; ROUTEID=.node1", "Content-Type: multipart/form-data OR application/json"],
        payload: "// Option A \u2014 JSON (file on server)\nPOST /Attachments2\n{\n  \"Attachments2_Lines\": [\n    {\n      \"SourcePath\":    \"/mnt/attachments/\",\n      \"FileName\":      \"invoice\",\n      \"FileExtension\": \"pdf\"\n    }\n  ]\n}\n\n// Option B \u2014 multipart form-data (remote file)\nPOST /Attachments2 HTTP/1.1\nContent-Type: multipart/form-data;boundary=BoundaryXYZ\n\n--BoundaryXYZ\nContent-Disposition: form-data; name=\"files\"; filename=\"invoice.pdf\"\nContent-Type: application/pdf\n\n<binary file content>\n--BoundaryXYZ--",
        response: "HTTP/1.1 201 Created\n{\n  \"AbsoluteEntry\": \"5\",\n  \"Attachments2_Lines\": [\n    {\n      \"SourcePath\":     \"/mnt/attachments/\",\n      \"FileName\":       \"invoice\",\n      \"FileExtension\":  \"pdf\",\n      \"AttachmentDate\": \"2024-01-20\",\n      \"UserID\":         \"1\"\n    }\n  ]\n}",
        notes: "Download: GET /Attachments2(5)/$value. Specific file: GET /Attachments2(5)/$value?filename='invoice.pdf'. PATCH for update.",
        queries: ["GET /Attachments2(5)/$value", "GET /Attachments2(5)/$value?filename='invoice.pdf'"],
      },
      {
        method: "GET / PUT", endpoint: "/ItemImages", etag: false,
        use: "Sync product images from catalog.",
        desc: "Get or update the main product image for an item.",
        headers: ["Cookie: B1SESSION=<token>; ROUTEID=.node1"],
        payload: "PUT /ItemImages('ITEM001')\nContent-Type: multipart/form-data;boundary=BoundaryIMG\n\n--BoundaryIMG\nContent-Disposition: form-data; name=\"files\"; filename=\"ITEM001.jpg\"\nContent-Type: image/jpeg\n\n<binary image data>\n--BoundaryIMG--",
        response: "HTTP/1.1 204 No Content",
        notes: null,
      },
      {
        method: "GET / POST / PATCH / DELETE", endpoint: "/ServiceLayerScripts", etag: false,
        use: "Server-side validation and custom business logic.",
        desc: "Deploy JavaScript extensions that run server-side inside Service Layer for custom validations and transformations.",
        headers: ["Cookie: B1SESSION=<token>; ROUTEID=.node1", "Content-Type: application/json (POST/PATCH)"],
        payload: "{\n  \"Code\":        \"OrderValidation\",\n  \"Description\": \"Validate platform order ID on creation\",\n  \"Script\":      \"function beforeCreateOrder(order) { if (!order.U_PlatformOrderID) throw new Error('Platform ID required'); }\"\n}",
        response: "HTTP/1.1 201 Created\n{ \"Code\": \"OrderValidation\", ... }",
        notes: null,
      },
      {
        method: "POST", endpoint: "/CompanyService_GetCompanyInfo", etag: false,
        use: "Read SAP company setup \u2014 base currency, feature flags.",
        desc: "Retrieve company-level SAP configuration \u2014 name, base currency, version, feature settings.",
        headers: ["Cookie: B1SESSION=<token>; ROUTEID=.node1", "Content-Type: application/json"],
        payload: "{}",
        response: "HTTP/1.1 200 OK\n{\n  \"CompanyInfo\": {\n    \"CompanyName\":  \"My Company Ltd\",\n    \"Version\":      1000110,\n    \"BaseCurrency\": \"INR\"\n  }\n}",
        notes: null,
      },
      {
        method: "POST", endpoint: "/CompanyService_UpdateCompanyInfo", etag: false,
        use: "Update SAP company configuration settings.",
        desc: "Update company-level SAP configuration settings.",
        headers: ["Cookie: B1SESSION=<token>; ROUTEID=.node1", "Content-Type: application/json"],
        payload: "{\n  \"CompanyInfo\": {\n    \"AutoCreateCustomerEqCard\": \"tYES\"\n  }\n}",
        response: "HTTP/1.1 204 No Content",
        notes: null,
      },
    ]
  },
];


const M_STYLE = {
  "GET":    { bg: "#DCFCE7", tx: "#166534" },
  "POST":   { bg: "#DBEAFE", tx: "#1E40AF" },
  "PATCH":  { bg: "#FEF3C7", tx: "#92400E" },
  "DELETE": { bg: "#FEE2E2", tx: "#991B1B" },
  "PUT":    { bg: "#FFF7ED", tx: "#9A3412" },
};

function Badge({ method }) {
  const parts = (method || "").replace(/ /g, "").split("/");
  return (
    <span style={{ display: "inline-flex", gap: 3, flexWrap: "wrap", flexShrink: 0 }}>
      {parts.map((p, i) => {
        const s = M_STYLE[p] || { bg: "#F3F4F6", tx: "#374151" };
        return <span key={i} style={{ background: s.bg, color: s.tx, fontSize: 10, fontWeight: 700, fontFamily: "monospace", padding: "2px 5px", borderRadius: 3, letterSpacing: 0.3, whiteSpace: "nowrap" }}>{p}</span>;
      })}
    </span>
  );
}

function CodeBlock({ code, lang }) {
  const [copied, setCopied] = useState(false);
  const copy = () => { try { navigator.clipboard.writeText(code); } catch(e) {} setCopied(true); setTimeout(() => setCopied(false), 1500); };
  return (
    <div style={{ background: "#0F172A", borderRadius: 8, overflow: "hidden", fontSize: 12, fontFamily: "monospace" }}>
      <div style={{ display: "flex", justifyContent: "space-between", padding: "5px 12px", borderBottom: "1px solid rgba(255,255,255,0.06)" }}>
        <span style={{ fontSize: 9, color: "#475569", textTransform: "uppercase", letterSpacing: 1 }}>{lang || ""}</span>
        <button onClick={copy} style={{ fontSize: 10, color: copied ? "#4ADE80" : "#475569", background: "none", border: "none", cursor: "pointer" }}>{copied ? "✓ copied" : "copy"}</button>
      </div>
      <pre style={{ margin: 0, padding: "10px 14px", color: "#E2E8F0", overflowX: "auto", whiteSpace: "pre-wrap", wordBreak: "break-word", lineHeight: 1.6 }}>{code}</pre>
    </div>
  );
}

function APICard({ api, color, bg }) {
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState("payload");
  const tabs = [
    ...(api.payload ? [{ id: "payload", label: "Payload" }] : []),
    { id: "response", label: "Response" },
    { id: "headers", label: "Headers" },
    ...(api.queries ? [{ id: "queries", label: "Queries" }] : []),
    ...(api.notes ? [{ id: "notes", label: "Notes" }] : []),
  ];
  const activeTab = tabs.find(t => t.id === tab) ? tab : (tabs[0] ? tabs[0].id : "response");

  return (
    <div style={{ borderBottom: "1px solid #F1F5F9", background: open ? bg : "#fff" }}>
      <div onClick={() => setOpen(o => !o)} style={{ display: "flex", alignItems: "center", gap: 10, padding: "10px 14px", cursor: "pointer", userSelect: "none" }}>
        <Badge method={api.method} />
        {api.etag && <span title="ETag supported" style={{ fontSize: 9, background: "#FEF3C7", color: "#92400E", padding: "1px 5px", borderRadius: 3, fontWeight: 700, whiteSpace: "nowrap" }}>ETAG</span>}
        <code style={{ fontSize: 13, fontWeight: 600, color: "#1E293B", flex: 1, fontFamily: "monospace" }}>{api.endpoint}</code>
        <span style={{ fontSize: 11, color: "#64748B", maxWidth: 240, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", display: open ? "none" : "block" }}>{api.use}</span>
        <span style={{ fontSize: 11, color: "#94A3B8" }}>{open ? "▲" : "▼"}</span>
      </div>
      {open && (
        <div style={{ padding: "0 14px 14px" }}>
          <p style={{ margin: "0 0 12px", fontSize: 13, color: "#334155", lineHeight: 1.65 }}>{api.desc}</p>
          <div style={{ display: "inline-flex", background: "#F1F5F9", padding: 3, borderRadius: 8, gap: 2, marginBottom: 12, flexWrap: "wrap" }}>
            {tabs.map(t => (
              <button key={t.id} onClick={() => setTab(t.id)} style={{
                padding: "4px 11px", borderRadius: 6, border: "none", cursor: "pointer", fontSize: 12, fontWeight: 500,
                background: activeTab === t.id ? "#fff" : "transparent",
                color: activeTab === t.id ? color : "#64748B",
                boxShadow: activeTab === t.id ? "0 1px 3px rgba(0,0,0,0.1)" : "none",
              }}>{t.label}</button>
            ))}
          </div>
          {activeTab === "payload" && (api.payload
            ? <CodeBlock code={api.payload} lang="Request Body" />
            : <p style={{ fontSize: 13, color: "#64748B", fontStyle: "italic", margin: 0 }}>No request body required.</p>
          )}
          {activeTab === "response" && <CodeBlock code={api.response} lang="Response" />}
          {activeTab === "headers" && (
            <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
              {(api.headers || []).map((h, i) => (
                <div key={i} style={{ background: "#0F172A", borderRadius: 6, padding: "7px 12px", fontFamily: "monospace", fontSize: 12 }}>
                  <span style={{ color: "#7DD3FC" }}>{h.split(":")[0]}</span>
                  <span style={{ color: "#94A3B8" }}>:</span>
                  <span style={{ color: "#E2E8F0" }}>{h.split(":").slice(1).join(":")}</span>
                </div>
              ))}
            </div>
          )}
          {activeTab === "queries" && api.queries && (
            <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
              {api.queries.map((q, i) => (
                <div key={i} style={{ background: "#0F172A", borderRadius: 6, padding: "7px 12px", fontFamily: "monospace", fontSize: 12, color: "#86EFAC", wordBreak: "break-all" }}>{q}</div>
              ))}
            </div>
          )}
          {activeTab === "notes" && api.notes && (
            <div style={{ background: "#FFFBEB", border: "1px solid #FDE68A", borderRadius: 8, padding: "10px 14px", fontSize: 13, color: "#78350F", lineHeight: 1.6 }}>⚠️ {api.notes}</div>
          )}
          <div style={{ marginTop: 10, display: "flex", gap: 6, alignItems: "flex-start" }}>
            <span style={{ fontSize: 10, fontWeight: 700, textTransform: "uppercase", letterSpacing: 0.5, color: color, paddingTop: 2, whiteSpace: "nowrap" }}>Use case</span>
            <span style={{ fontSize: 13, color: "#1E293B", fontWeight: 500 }}>{api.use}</span>
          </div>
        </div>
      )}
    </div>
  );
}

export default function App() {
  const [search, setSearch] = useState("");
  const [mainTab, setMainTab] = useState("apis");
  const [activeCat, setActiveCat] = useState("all");

  const total = categories.reduce((s, c) => s + c.apis.length, 0);

  const visible = categories
    .filter(c => activeCat === "all" || c.id === activeCat)
    .map(c => ({
      ...c,
      apis: c.apis.filter(a =>
        !search.trim() ||
        a.endpoint.toLowerCase().includes(search.toLowerCase()) ||
        a.desc.toLowerCase().includes(search.toLowerCase()) ||
        a.use.toLowerCase().includes(search.toLowerCase())
      ),
    }))
    .filter(c => c.apis.length > 0);

  return (
    <div style={{ fontFamily: "system-ui, -apple-system, sans-serif", maxWidth: 900, margin: "0 auto", paddingBottom: 60 }}>
      <div style={{ background: "linear-gradient(135deg, #0F2544 0%, #1E3A5F 100%)", borderRadius: 12, padding: "22px 24px", marginBottom: 18, color: "#fff" }}>
        <div style={{ fontSize: 10, letterSpacing: 2, textTransform: "uppercase", color: "#7DD3FC", marginBottom: 5 }}>SAP Business One · Service Layer</div>
        <div style={{ fontSize: 21, fontWeight: 700, marginBottom: 5 }}>Complete API Reference</div>
        <div style={{ fontSize: 13, color: "#94A3B8", display: "flex", flexWrap: "wrap", gap: 12, alignItems: "center" }}>
          <code style={{ background: "rgba(255,255,255,0.1)", padding: "2px 8px", borderRadius: 4, color: "#7DD3FC", fontFamily: "monospace" }}>https://&#123;server&#125;:50000/b1s/v2</code>
          <span>{total} endpoints · {categories.length} categories · OData v4 · Session-cookie auth</span>
        </div>
      </div>

      <div style={{ display: "flex", gap: 3, marginBottom: 18, background: "#F1F5F9", padding: 4, borderRadius: 10, width: "fit-content" }}>
        {[["apis","📋 APIs"],["query","🔍 Query Options"],["errors","⚠️ Error Reference"]].map(([id, label]) => (
          <button key={id} onClick={() => setMainTab(id)} style={{
            padding: "7px 16px", borderRadius: 7, border: "none", cursor: "pointer", fontSize: 13, fontWeight: 500,
            background: mainTab === id ? "#fff" : "transparent",
            color: mainTab === id ? "#1E3A5F" : "#64748B",
            boxShadow: mainTab === id ? "0 1px 3px rgba(0,0,0,0.08)" : "none",
          }}>{label}</button>
        ))}
      </div>

      {mainTab === "apis" && (
        <>
          <div style={{ position: "relative", marginBottom: 12 }}>
            <input value={search} onChange={e => setSearch(e.target.value)}
              placeholder="Search endpoints, descriptions, use cases..."
              style={{ width: "100%", boxSizing: "border-box", padding: "10px 14px 10px 36px", border: "1.5px solid #E2E8F0", borderRadius: 8, fontSize: 13, outline: "none", background: "#fff", color: "#1E293B" }} />
            <span style={{ position: "absolute", left: 12, top: "50%", transform: "translateY(-50%)", color: "#94A3B8" }}>🔍</span>
          </div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 5, marginBottom: 18 }}>
            <button onClick={() => setActiveCat("all")} style={{ padding: "5px 12px", borderRadius: 6, fontSize: 11, fontWeight: 600, cursor: "pointer", border: "1.5px solid", background: activeCat === "all" ? "#1E3A5F" : "#fff", color: activeCat === "all" ? "#fff" : "#374151", borderColor: activeCat === "all" ? "#1E3A5F" : "#E2E8F0" }}>All ({total})</button>
            {categories.map(c => (
              <button key={c.id} onClick={() => setActiveCat(c.id)} style={{ padding: "5px 12px", borderRadius: 6, fontSize: 11, fontWeight: 600, cursor: "pointer", border: "1.5px solid", background: activeCat === c.id ? c.color : "#fff", color: activeCat === c.id ? "#fff" : "#374151", borderColor: activeCat === c.id ? c.color : "#E2E8F0" }}>
                {c.icon} {c.label} ({c.apis.length})
              </button>
            ))}
          </div>
          {visible.length === 0
            ? <div style={{ textAlign: "center", color: "#94A3B8", padding: "60px 0" }}>No APIs match "{search}"</div>
            : visible.map(cat => (
              <div key={cat.id} style={{ marginBottom: 20 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
                  <div style={{ width: 4, height: 20, borderRadius: 2, background: cat.color }} />
                  <span style={{ fontWeight: 700, fontSize: 14, color: cat.color }}>{cat.icon} {cat.label}</span>
                  <span style={{ fontSize: 11, color: "#94A3B8", background: "#F1F5F9", padding: "1px 8px", borderRadius: 10 }}>{cat.apis.length} endpoints</span>
                </div>
                <div style={{ border: "1.5px solid #E2E8F0", borderRadius: 10, overflow: "hidden" }}>
                  {cat.apis.map((api, i) => <APICard key={i} api={api} color={cat.color} bg={cat.bg} />)}
                </div>
              </div>
            ))
          }
        </>
      )}

      {mainTab === "query" && (
        <div>
          <p style={{ fontSize: 13, color: "#475569", marginBottom: 16, lineHeight: 1.65 }}>
            Append these OData parameters to any GET request. Base: <code style={{ fontFamily: "monospace", background: "#F1F5F9", padding: "1px 6px", borderRadius: 4 }}>GET /b1s/v2/Orders?$filter=...</code>
          </p>
          <div style={{ border: "1.5px solid #E2E8F0", borderRadius: 10, overflow: "hidden", marginBottom: 20 }}>
            {QUERY_OPTIONS.map((q, i) => (
              <div key={i} style={{ padding: "12px 16px", borderBottom: i < QUERY_OPTIONS.length - 1 ? "1px solid #F1F5F9" : "none" }}>
                <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 6 }}>
                  <code style={{ fontFamily: "monospace", fontSize: 12, fontWeight: 700, color: "#6B21A8", background: "#F5F3FF", padding: "2px 8px", borderRadius: 4 }}>{q.opt}</code>
                  <span style={{ fontSize: 13, color: "#475569" }}>{q.desc}</span>
                </div>
                <CodeBlock code={q.ex} lang="example" />
              </div>
            ))}
          </div>
          <CodeBlock lang="Pagination Pattern" code={"// Page 1\nGET /PurchaseOrders?$top=50&$skip=0&$filter=DocumentStatus eq 'bost_Open'\n\n// Page 2\nGET /PurchaseOrders?$top=50&$skip=50\n\n// Or follow nextLink from response:\n// \"@odata.nextLink\": \"/b1s/v2/Orders?$skip=20\"\n\n// Disable pagination (return all):\nGET /Orders\nPrefer: odata.maxpagesize=0"} />
        </div>
      )}

      {mainTab === "errors" && (
        <div>
          <p style={{ fontSize: 13, color: "#475569", marginBottom: 16 }}>Every error has the same JSON shape. Check HTTP status first, then read <code style={{ fontFamily: "monospace", background: "#F1F5F9", padding: "1px 6px", borderRadius: 4 }}>error.code</code> and <code style={{ fontFamily: "monospace", background: "#F1F5F9", padding: "1px 6px", borderRadius: 4 }}>error.message.value</code>.</p>
          <CodeBlock lang="Error Shape (all errors)" code={"{\n  \"error\": {\n    \"code\": -10,\n    \"message\": {\n      \"lang\": \"en-us\",\n      \"value\": \"1320000140 - Business partner code V001 already assigned\"\n    }\n  }\n}"} />
          <h3 style={{ fontSize: 14, fontWeight: 700, color: "#334155", margin: "20px 0 10px" }}>HTTP Status Codes</h3>
          <div style={{ border: "1.5px solid #E2E8F0", borderRadius: 10, overflow: "hidden", marginBottom: 20 }}>
            {HTTP_CODES.map((c, i) => (
              <div key={i} style={{ display: "flex", gap: 12, padding: "11px 16px", borderBottom: i < HTTP_CODES.length - 1 ? "1px solid #F1F5F9" : "none", alignItems: "flex-start" }}>
                <code style={{ fontFamily: "monospace", fontSize: 11, fontWeight: 700, padding: "3px 8px", borderRadius: 5, background: c.bg, color: c.color, whiteSpace: "nowrap" }}>{c.code}</code>
                <span style={{ fontSize: 13, color: "#334155", lineHeight: 1.6 }}>{c.desc}</span>
              </div>
            ))}
          </div>
          <h3 style={{ fontSize: 14, fontWeight: 700, color: "#334155", margin: "0 0 10px" }}>SAP Internal Error Codes</h3>
          <div style={{ border: "1.5px solid #E2E8F0", borderRadius: 10, overflow: "hidden" }}>
            {SAP_ERRORS.map((e, i) => (
              <div key={i} style={{ display: "flex", gap: 12, padding: "10px 16px", borderBottom: i < SAP_ERRORS.length - 1 ? "1px solid #F1F5F9" : "none", alignItems: "center" }}>
                <code style={{ fontFamily: "monospace", fontSize: 11, fontWeight: 700, color: "#B91C1C", background: "#FEF2F2", padding: "2px 8px", borderRadius: 4, whiteSpace: "nowrap" }}>{e.code}</code>
                <span style={{ fontSize: 13, color: "#334155" }}>{e.desc}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      <div style={{ marginTop: 28, fontSize: 11, color: "#94A3B8", textAlign: "center", lineHeight: 1.7 }}>
        Base path: <code style={{ fontFamily: "monospace" }}>/b1s/v2</code> (OData v4, recommended) or <code style={{ fontFamily: "monospace" }}>/b1s/v1</code> (OData v3)<br/>
        Every request requires <code style={{ fontFamily: "monospace" }}>Cookie: B1SESSION=&lt;token&gt;; ROUTEID=.node1</code>
      </div>
    </div>
  );
}
