import { Fragment, useEffect, useState } from "react";
import {
  ClipboardList, RefreshCcw, Wifi, WifiOff, Clock,
  ChevronDown, ChevronRight, Plus, PackageCheck,
  CheckCircle, XCircle, AlertCircle, Loader2, Webhook, FilePen,
} from "lucide-react";
import {
  getBlinkitPOs, getBlinkitPO, getBlinkitHealth, createBlinkitASN,
  getBlinkitASNs, getBlinkitPOSKUAllocations, requestBlinkitPOAmendment,
} from "../api";
import type {
  BlinkitPO, BlinkitPOItem, BlinkitPOListData, BlinkitTrackedASN,
} from "../types";
import blinkitLogo from "../assets/blinkit-logo.svg";

const PO_STATUS_STYLE: Record<string, string> = {
  OPEN:     "bg-green-100 text-green-700 border-green-300",
  RELEASED: "bg-green-100 text-green-700 border-green-300",
  DRAFT:    "bg-yellow-100 text-yellow-700 border-yellow-300",
  CLOSED:   "bg-blue-100 text-blue-700 border-blue-300",
  CANCELLED:"bg-red-100 text-red-700 border-red-300",
  EXPIRED:  "bg-gray-100 text-gray-600 border-gray-300",
};

interface AmendItemForm {
  item_id: string;
  productName: string;
  upc: string;
  mrp: number;
  uom_type: string;
  uom_value: string;
  uom_unit: string;
  include: boolean;
}

interface AmendResult {
  success: boolean;
  message?: string;
  updated_items?: any[];
}

interface ASNLineForm {
  productId: string;
  skuCode: string;
  upc: string;
  productName: string;
  hsnCode: string;
  requestedQty: number;
  invoicedQty: number;
  rate: number;
  mrp: number;
  batchNumber: string;
  mfgDate: string;
  expiryDate: string;
  cgstPct: number;
  sgstPct: number;
  igstPct: number;
}

interface CreateASNForm {
  invoiceNumber: string;
  invoiceDate: string;
  deliveryDate: string;
  supplierGstin: string;
  supplierAddress1: string;
  supplierCity: string;
  supplierState: string;
  supplierPostalCode: string;
  buyerGstin: string;
  deliveryType: string;
  deliveryPartner: string;
  trackingCode: string;
  eWayBill: string;
  licenseNumber: string;
  driverPhone: string;
  lines: ASNLineForm[];
}

function recalcLineQtys(
  lines: ASNLineForm[],
  allocations: Record<string, number>,
): ASNLineForm[] {
  if (Object.keys(allocations).length === 0) return lines.map(l => ({ ...l, invoicedQty: l.requestedQty }));
  return lines.map(l => ({
    ...l,
    invoicedQty: Math.max(0, l.requestedQty - (allocations[l.productId] ?? 0)),
  }));
}

function lineFromItem(item: BlinkitPOItem): ASNLineForm {
  return {
    productId:    item.productId,
    skuCode:      item.skuCode ?? "",
    upc:          item.upc ?? "",
    productName:  item.productName,
    hsnCode:      item.hsnCode ?? "",
    requestedQty: item.requestedQty,
    invoicedQty:  item.requestedQty,
    rate:         item.rate,
    mrp:          item.mrp,
    batchNumber:  "",
    mfgDate:      "",
    expiryDate:   "",
    cgstPct:      item.cgstRate ?? 0,
    sgstPct:      item.sgstRate ?? 0,
    igstPct:      item.igstRate ?? 0,
  };
}

export default function BlinkitPOs() {
  const [pos, setPOs]               = useState<BlinkitPO[]>([]);
  const [loading, setLoading]       = useState(false);
  const [connected, setConnected]   = useState<boolean | null>(null);
  const [expandedRows, setExpanded] = useState<Set<string>>(new Set());
  const [loadingItems, setLoadingItems] = useState<Set<string>>(new Set());

  // ASN modal
  const [asnModal, setAsnModal]           = useState<BlinkitPO | null>(null);
  const [asnForm, setAsnForm]             = useState<CreateASNForm>({
    invoiceNumber: "", invoiceDate: "", deliveryDate: "",
    supplierGstin: "", supplierAddress1: "", supplierCity: "",
    supplierState: "", supplierPostalCode: "", buyerGstin: "",
    deliveryType: "SELF", lines: [],
  });
  const [asnSubmitting, setAsnSubmitting]         = useState(false);
  const [asnResult, setAsnResult]                 = useState<string | null>(null);
  const [asnError, setAsnError]                   = useState<string | null>(null);
  const [asnExisting, setAsnExisting]             = useState<BlinkitTrackedASN[]>([]);
  const [asnAllocations, setAsnAllocations]       = useState<Record<string, number>>({});
  const [asnExistingLoading, setAsnExistingLoading] = useState(false);
  const [poAllocationsMap, setPoAllocationsMap]     = useState<Record<string, Record<string, number>>>({});

  // Amendment modal
  const [amendModal, setAmendModal]         = useState<BlinkitPO | null>(null);
  const [amendLines, setAmendLines]         = useState<AmendItemForm[]>([]);
  const [amendSubmitting, setAmendSubmitting] = useState(false);
  const [amendResult, setAmendResult]       = useState<AmendResult | null>(null);
  const [amendError, setAmendError]         = useState<string | null>(null);

  const fetchAllAllocations = async (poList: BlinkitPO[]) => {
    const results = await Promise.allSettled(
      poList.map(po => getBlinkitPOSKUAllocations(po.purchaseOrderId))
    );
    const map: Record<string, Record<string, number>> = {};
    results.forEach((r, i) => {
      if (r.status === "fulfilled") {
        map[poList[i].purchaseOrderId] = r.value.data?.allocations ?? {};
      }
    });
    setPoAllocationsMap(map);
  };

  const checkHealth = async () => {
    try {
      const r = await getBlinkitHealth();
      setConnected(r.data?.connectivity?.reachable !== false);
    } catch {
      setConnected(false);
    }
  };

  const fetchPOs = async () => {
    setLoading(true);
    try {
      const res  = await getBlinkitPOs();
      const body  = res.data?.data ?? res.data;
      const list  = (body?.purchaseOrders ?? body?.data?.purchaseOrders ?? body) as BlinkitPO[];
      const poList = Array.isArray(list) ? list : [];
      setPOs(poList);
      fetchAllAllocations(poList);
    } catch (e: any) {
      console.error("Blinkit PO fetch error:", e);
      setPOs([]);
    }
    setLoading(false);
  };

  const getFillRate = (po: BlinkitPO): number => {
    const alloc = poAllocationsMap[po.purchaseOrderId];
    if (!alloc || !po.totalQty) return 0;
    const allocated = Object.values(alloc).reduce((s, v) => s + v, 0);
    return Math.min(100, Math.round((allocated / po.totalQty) * 100));
  };

  useEffect(() => {
    checkHealth();
    fetchPOs();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const toggleRow = async (po: BlinkitPO) => {
    const id = po.purchaseOrderId;
    const next = new Set(expandedRows);
    if (next.has(id)) {
      next.delete(id);
      setExpanded(next);
      return;
    }
    next.add(id);
    setExpanded(next);
    // Lazy-load line items if not yet fetched
    if (!po.items?.length) {
      setLoadingItems(prev => new Set(prev).add(id));
      try {
        const res   = await getBlinkitPO(id);
        const detail = res.data?.data ?? res.data;
        setPOs(prev =>
          prev.map(p =>
            p.purchaseOrderId === id
              ? { ...p, items: detail?.items ?? detail?.data?.items ?? [] }
              : p,
          ),
        );
      } catch { /* non-critical */ }
      setLoadingItems(prev => {
        const s = new Set(prev);
        s.delete(id);
        return s;
      });
    }
  };

  const openASNModal = async (po: BlinkitPO) => {
    setAsnResult(null);
    setAsnError(null);
    setAsnExisting([]);
    setAsnAllocations({});
    const baseLines = (po.items ?? []).map(lineFromItem);
    setAsnForm({
      invoiceNumber:      "",
      invoiceDate:        new Date().toISOString().split("T")[0],
      deliveryDate:       po.deliveryDate ? po.deliveryDate.split("T")[0] : "",
      supplierGstin:      "",
      supplierAddress1:   "",
      supplierCity:       "",
      supplierState:      "",
      supplierPostalCode: "",
      buyerGstin:         po.buyerGstin ?? "",
      deliveryType:       "SELF",
      deliveryPartner:    "",
      trackingCode:       "",
      eWayBill:           "",
      licenseNumber:      "",
      driverPhone:        "",
      lines:              baseLines,
    });
    setAsnModal(po);
    setAsnExistingLoading(true);
    try {
      const [asnRes, allocRes] = await Promise.all([
        getBlinkitASNs(po.purchaseOrderId),
        getBlinkitPOSKUAllocations(po.purchaseOrderId),
      ]);
      const existing   = (asnRes.data?.data?.asns ?? []) as BlinkitTrackedASN[];
      const allocations = (allocRes.data?.allocations ?? {}) as Record<string, number>;
      setAsnExisting(existing);
      setAsnAllocations(allocations);
      setAsnForm(f => ({ ...f, lines: recalcLineQtys(f.lines, allocations) }));
    } catch { /* non-critical */ }
    setAsnExistingLoading(false);
  };

  const handleASNSubmit = async (e: { preventDefault(): void }) => {
    e.preventDefault();
    if (!asnModal) return;
    setAsnSubmitting(true);
    setAsnError(null);
    const shipLines = asnForm.lines.filter(l => l.invoicedQty > 0);
    const totalQty       = shipLines.reduce((s, l) => s + l.invoicedQty, 0);
    const totalBasic     = shipLines.reduce((s, l) => s + l.rate * l.invoicedQty, 0);
    const totalLanding   = shipLines.reduce((s, l) => s + l.rate * l.invoicedQty, 0);

    // Compute po_status: PO_FULFILLED if every item at 0 remaining, else PARTIALLY_FULFILLED
    const poAllocAfterSubmit = { ...asnAllocations };
    shipLines.forEach(l => {
      poAllocAfterSubmit[l.productId] = (poAllocAfterSubmit[l.productId] ?? 0) + l.invoicedQty;
    });
    const allFilled = asnForm.lines.every(
      l => (poAllocAfterSubmit[l.productId] ?? 0) >= l.requestedQty
    );
    const poStatus = allFilled ? "PO_FULFILLED" : "PARTIALLY_FULFILLED";

    // Header-level tax rollup
    const taxMap: Record<string, { pct: number; total: number }> = {};
    shipLines.forEach(l => {
      const cgstTotal = (l.cgstPct / 100) * l.rate * l.invoicedQty;
      const sgstTotal = (l.sgstPct / 100) * l.rate * l.invoicedQty;
      const igstTotal = (l.igstPct / 100) * l.rate * l.invoicedQty;
      if (l.cgstPct > 0) {
        taxMap["CGST"] = { pct: l.cgstPct, total: (taxMap["CGST"]?.total ?? 0) + cgstTotal };
        taxMap["SGST"] = { pct: l.sgstPct, total: (taxMap["SGST"]?.total ?? 0) + sgstTotal };
      }
      if (l.igstPct > 0) {
        taxMap["IGST"] = { pct: l.igstPct, total: (taxMap["IGST"]?.total ?? 0) + igstTotal };
      }
    });
    const headerTax = Object.entries(taxMap).map(([type, v]) => ({
      gst_type: type, gst_percentage: v.pct, gst_total: parseFloat(v.total.toFixed(2)),
    }));
    if (headerTax.length === 0) {
      headerTax.push(
        { gst_type: "CGST", gst_percentage: 0, gst_total: 0 },
        { gst_type: "SGST", gst_percentage: 0, gst_total: 0 },
      );
    }

    const shipmentDetails: Record<string, string> = {
      delivery_type: asnForm.deliveryType,
    };
    if (asnForm.eWayBill)       shipmentDetails["e_way_bill_number"]      = asnForm.eWayBill;
    if (asnForm.licenseNumber)  shipmentDetails["license_number"]         = asnForm.licenseNumber;
    if (asnForm.driverPhone)    shipmentDetails["driver_phone_number"]    = asnForm.driverPhone;
    if (asnForm.deliveryType === "COURIER") {
      shipmentDetails["delivery_partner"]      = asnForm.deliveryPartner;
      shipmentDetails["delivery_tracking_code"] = asnForm.trackingCode;
    }

    const payload = {
      po_number:      asnModal.purchaseOrderId,
      invoice_number: asnForm.invoiceNumber,
      invoice_date:   asnForm.invoiceDate,
      delivery_date:  asnForm.deliveryDate || undefined,
      po_status:      poStatus,
      quantity:       String(totalQty),
      item_count:     String(shipLines.length),
      basic_price:    String(totalBasic.toFixed(2)),
      landing_price:  String(totalLanding.toFixed(2)),
      tax_distribution: headerTax,
      supplier_details: {
        name:  "Let's Try Foods",
        gstin: asnForm.supplierGstin,
        supplier_address: {
          address_line_1: asnForm.supplierAddress1,
          city:           asnForm.supplierCity,
          state:          asnForm.supplierState,
          postal_code:    asnForm.supplierPostalCode,
          country:        "India",
        },
      },
      buyer_details:    { gstin: asnForm.buyerGstin },
      shipment_details: shipmentDetails,
      items: shipLines.map(l => ({
        item_id:            String(l.productId),
        sku_code:           l.skuCode || undefined,
        sku_description:    l.productName,
        upc:                l.upc || "",
        batch_number:       l.batchNumber || "",
        case_config:        1,
        quantity:           l.invoicedQty,
        mrp:                l.mrp,
        unit_basic_price:   l.rate,
        unit_landing_price: String(l.rate),
        uom:                { unit: "PC", value: 1 },
        ...(l.hsnCode     ? { hsn_code:    l.hsnCode }    : {}),
        ...(l.expiryDate  ? { expiry_date: l.expiryDate } : {}),
        ...(l.mfgDate     ? { mfg_date:    l.mfgDate }    : {}),
        tax_distribution: {
          cgst_percentage:       l.cgstPct,
          sgst_percentage:       l.sgstPct,
          igst_percentage:       l.igstPct,
          ugst_percentage:       0,
          cess_percentage:       0,
          additional_cess_value: 0,
        },
      })),
    };
    try {
      const res  = await createBlinkitASN(payload);
      const id   = res.data?.asn_id ?? res.data?.data?.asnId ?? res.data?.asnId;
      setAsnResult(id ?? "ASN Submitted");
      // Refresh allocations so fill rate updates immediately
      try {
        const [asnRes, allocRes] = await Promise.all([
          getBlinkitASNs(asnModal.purchaseOrderId),
          getBlinkitPOSKUAllocations(asnModal.purchaseOrderId),
        ]);
        const newExisting    = (asnRes.data?.data?.asns ?? []) as BlinkitTrackedASN[];
        const newAllocations = (allocRes.data?.allocations ?? {}) as Record<string, number>;
        setAsnExisting(newExisting);
        setAsnAllocations(newAllocations);
        setPoAllocationsMap(prev => ({ ...prev, [asnModal.purchaseOrderId]: newAllocations }));
      } catch { /* non-critical */ }
    } catch (err: any) {
      setAsnError(
        err.response?.data?.detail ??
        JSON.stringify(err.response?.data) ??
        "Failed to submit ASN",
      );
    }
    setAsnSubmitting(false);
  };

  const openAmendModal = async (po: BlinkitPO) => {
    setAmendResult(null);
    setAmendError(null);
    // If items not yet loaded, fetch them first
    let items = po.items ?? [];
    if (!items.length) {
      try {
        const res = await getBlinkitPO(po.purchaseOrderId);
        const detail = res.data?.data ?? res.data;
        items = detail?.items ?? [];
        setPOs(prev => prev.map(p =>
          p.purchaseOrderId === po.purchaseOrderId ? { ...p, items } : p
        ));
      } catch { /* non-critical */ }
    }
    setAmendLines(items.map(item => ({
      item_id:     item.productId,
      productName: item.productName,
      upc:         item.upc ?? "",
      mrp:         item.mrp,
      uom_type:    "STANDARD",
      uom_value:   String(item.unitValue ?? ""),
      uom_unit:    item.unitLabel ?? "",
      include:     false,
    })));
    setAmendModal(po);
  };

  const handleAmendSubmit = async (e: { preventDefault(): void }) => {
    e.preventDefault();
    if (!amendModal) return;
    const selected = amendLines.filter(l => l.include);
    if (!selected.length) {
      setAmendError("Select at least one item to amend.");
      return;
    }
    setAmendSubmitting(true);
    setAmendError(null);
    const request_data = selected.map(l => ({
      item_id:  l.item_id,
      variants: [{
        upc:        l.upc,
        mrp:        l.mrp,
        uom: {
          type:  l.uom_type,
          value: l.uom_value,
          unit:  l.uom_unit,
        },
        po_numbers: [amendModal.purchaseOrderId],
      }],
    }));
    try {
      const res = await requestBlinkitPOAmendment(amendModal.purchaseOrderId, { request_data });
      setAmendResult({ success: true, message: res.data?.data?.message ?? "Amendment submitted", updated_items: res.data?.data?.updated_items });
    } catch (err: any) {
      setAmendError(
        err.response?.data?.detail ??
        JSON.stringify(err.response?.data) ??
        "Failed to submit amendment",
      );
    }
    setAmendSubmitting(false);
  };

  const stats = {
    total:    pos.length,
    open:     pos.filter(p => p.status === "OPEN").length,
    closed:   pos.filter(p => p.status === "CLOSED" || p.status === "CANCELLED").length,
    totalQty: pos.reduce((s, p) => s + (p.totalQty ?? 0), 0),
  };

  return (
    <div className="space-y-6">

      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <img src={blinkitLogo} alt="Blinkit" className="w-10 h-10 rounded-xl" />
          <div>
            <h1 className="text-2xl font-bold text-gray-900">Blinkit Purchase Orders</h1>
            <p className="text-sm text-gray-500 mt-0.5">
              Vendor ID: 18309 · Testing (dev.partnersbiz.com)
            </p>
          </div>
        </div>
        <div className="flex items-center gap-3">
          <div className={`flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium ${
            connected === true  ? "bg-green-50 text-green-700"  :
            connected === false ? "bg-red-50 text-red-700"      :
                                  "bg-gray-50 text-gray-500"
          }`}>
            {connected === true  ? <Wifi size={13} />    :
             connected === false ? <WifiOff size={13} /> : <Clock size={13} />}
            {connected === true  ? "Blinkit Connected" :
             connected === false ? "Connection Error"  : "Checking…"}
          </div>
          <button
            onClick={() => { checkHealth(); fetchPOs(); }}
            className="flex items-center gap-2 px-4 py-2 border border-gray-200 rounded-lg text-sm hover:bg-gray-50 transition"
          >
            <RefreshCcw size={14} /> Refresh
          </button>
        </div>
      </div>

      {/* Inbound webhook info */}
      <div className="bg-amber-50 border border-amber-200 rounded-xl p-4 text-sm text-amber-900 flex items-start gap-3">
        <Webhook size={16} className="mt-0.5 flex-shrink-0 text-amber-600" />
        <div className="space-y-1">
          <p>
            <strong>How Blinkit POs work:</strong> Blinkit <em>pushes</em> PO events to our
            webhook — there is no pull API. POs appear here once Blinkit's team configures
            our webhook URL.
          </p>
          <p className="text-xs text-amber-700 font-mono break-all">
            Webhook URL to share with Blinkit:{" "}
            <span className="font-semibold">
              https://po-integration-backend.onrender.com/api/webhook/inbound/blinkit/po
            </span>
          </p>
        </div>
      </div>


      {/* Stats */}
      <div className="grid grid-cols-4 gap-4">
        {[
          { label: "Total POs",        value: stats.total,                     icon: ClipboardList, color: "bg-amber-500"  },
          { label: "Open / Active",    value: stats.open,                      icon: CheckCircle,   color: "bg-green-500"  },
          { label: "Closed / Cancelled", value: stats.closed,                  icon: XCircle,       color: "bg-red-500"    },
          { label: "Total Qty",        value: stats.totalQty.toLocaleString(), icon: PackageCheck,  color: "bg-blue-500"   },
        ].map(({ label, value, icon: Icon, color }) => (
          <div key={label} className="bg-white rounded-xl p-4 shadow-sm border border-gray-100 flex items-center gap-3">
            <div className={`w-10 h-10 ${color} rounded-lg flex items-center justify-center flex-shrink-0`}>
              <Icon size={18} className="text-white" />
            </div>
            <div>
              <p className="text-xs text-gray-500">{label}</p>
              <p className="text-xl font-bold text-gray-800">{value}</p>
            </div>
          </div>
        ))}
      </div>

      {/* PO Table */}
      <div className="bg-white rounded-xl shadow-sm border border-gray-100 overflow-hidden">
        {loading ? (
          <div className="text-center py-16 text-gray-400">
            <Loader2 size={28} className="mx-auto mb-3 animate-spin text-amber-400" />
            Loading Blinkit POs from webhook store…
          </div>
        ) : pos.length === 0 ? (
          <div className="text-center py-16 text-gray-400">
            <Webhook size={40} className="mx-auto mb-3 text-gray-300" />
            <p className="font-medium">Waiting for Blinkit to push POs</p>
            <p className="text-sm mt-1 max-w-sm mx-auto">
              POs will appear here once Blinkit configures our inbound webhook URL.
              Share: <span className="font-mono text-xs text-amber-700">
                /api/webhook/inbound/blinkit/po
              </span>
            </p>
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-gray-500 text-xs uppercase">
              <tr>
                <th className="px-4 py-3 text-left w-8"></th>
                <th className="px-4 py-3 text-left">PO Number</th>
                <th className="px-4 py-3 text-left">Warehouse</th>
                <th className="px-4 py-3 text-left">Status</th>
                <th className="px-4 py-3 text-left">Received At</th>
                <th className="px-4 py-3 text-left">Delivery Date</th>
                <th className="px-4 py-3 text-right">Total Qty</th>
                <th className="px-4 py-3 text-right">Amount</th>
                <th className="px-4 py-3 text-center">Fill Rate</th>
                <th className="px-4 py-3 text-center">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-50">
              {pos.map(po => {
                const expanded    = expandedRows.has(po.purchaseOrderId);
                const itemLoading = loadingItems.has(po.purchaseOrderId);
                return (
                  <Fragment key={po.purchaseOrderId}>
                    <tr className={`hover:bg-gray-50 ${expanded ? "bg-amber-50/30" : ""}`}>
                      <td className="px-4 py-3">
                        <button
                          onClick={() => toggleRow(po)}
                          className="text-gray-400 hover:text-amber-600 transition"
                        >
                          {itemLoading
                            ? <Loader2 size={16} className="animate-spin" />
                            : expanded
                              ? <ChevronDown size={16} />
                              : <ChevronRight size={16} />}
                        </button>
                      </td>
                      <td className="px-4 py-3">
                        <div className="font-mono text-xs font-semibold text-amber-700">
                          {po.purchaseOrderId}
                        </div>
                        {po.poCode && (
                          <div className="text-gray-400 text-xs">{po.poCode}</div>
                        )}
                      </td>
                      <td className="px-4 py-3">
                        <div className="text-gray-800 text-xs font-medium">
                          {po.warehouseName ?? po.warehouseCode ?? "—"}
                        </div>
                        {po.cityName && (
                          <div className="text-gray-400 text-xs">{po.cityName}</div>
                        )}
                      </td>
                      <td className="px-4 py-3">
                        <span className={`px-2 py-0.5 rounded-full text-xs font-semibold border ${
                          PO_STATUS_STYLE[po.status] ?? "bg-gray-100 text-gray-600 border-gray-200"
                        }`}>
                          {po.status}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-gray-500 text-xs whitespace-nowrap">
                        {(po.receivedAt ?? po.createdAt)
                          ? new Date((po.receivedAt ?? po.createdAt)!).toLocaleString("en-IN", {
                              day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit",
                            })
                          : "—"}
                      </td>
                      <td className="px-4 py-3 text-gray-500 text-xs whitespace-nowrap">
                        {po.deliveryDate
                          ? new Date(po.deliveryDate).toLocaleDateString("en-IN", {
                              day: "2-digit", month: "short", year: "numeric",
                            })
                          : "—"}
                      </td>
                      <td className="px-4 py-3 text-right font-semibold text-gray-700">
                        {(po.totalQty ?? 0).toLocaleString()}
                      </td>
                      <td className="px-4 py-3 text-right text-gray-600 text-xs">
                        {po.totalAmount != null
                          ? `₹${po.totalAmount.toLocaleString("en-IN", { maximumFractionDigits: 2 })}`
                          : "—"}
                      </td>
                      <td className="px-4 py-3 text-center">
                        {(() => {
                          const rate = getFillRate(po);
                          const color = rate >= 100
                            ? "bg-red-100 text-red-700 border-red-300"
                            : rate > 0
                              ? "bg-yellow-100 text-yellow-700 border-yellow-300"
                              : "bg-gray-100 text-gray-500 border-gray-200";
                          return (
                            <span className={`px-2 py-0.5 rounded-full text-xs font-semibold border ${color}`}>
                              {rate}%
                            </span>
                          );
                        })()}
                      </td>
                      <td className="px-4 py-3 text-center">
                        {po.status === "CANCELLED" || po.status === "EXPIRED" ? (
                          <span className="text-xs text-gray-400 italic">
                            {po.status === "CANCELLED" ? "Cancelled" : "Expired"}
                          </span>
                        ) : (po.status === "OPEN" || po.status === "RELEASED") && (() => {
                          const isFull = getFillRate(po) >= 100;
                          return (
                            <div className="flex items-center justify-center gap-1.5">
                              <button
                                onClick={() => !isFull && openASNModal(po)}
                                disabled={isFull}
                                title={isFull ? "All items fully invoiced" : "Create ASN"}
                                className={`inline-flex items-center gap-1 px-2.5 py-1.5 text-xs rounded-lg transition ${
                                  isFull
                                    ? "bg-gray-100 text-gray-400 cursor-not-allowed"
                                    : "bg-amber-500 text-white hover:bg-amber-600"
                                }`}
                              >
                                <Plus size={11} /> {isFull ? "Full" : "ASN"}
                              </button>
                              <button
                                onClick={() => openAmendModal(po)}
                                title="Request PO Amendment (correct MRP / UOM / UPC)"
                                className="inline-flex items-center gap-1 px-2.5 py-1.5 text-xs rounded-lg border border-blue-200 text-blue-600 hover:bg-blue-50 transition"
                              >
                                <FilePen size={11} /> Amend
                              </button>
                            </div>
                          );
                        })()}
                      </td>
                    </tr>

                    {/* Expanded line items */}
                    {expanded && (
                      <tr>
                        <td colSpan={10} className="bg-amber-50/40 px-8 py-3 border-b border-amber-100">
                          {itemLoading ? (
                            <div className="flex items-center gap-2 text-xs text-gray-400 py-2">
                              <Loader2 size={12} className="animate-spin" /> Loading line items…
                            </div>
                          ) : !po.items?.length ? (
                            <p className="text-xs text-gray-400 italic py-1">No line items available</p>
                          ) : (
                            <table className="w-full text-xs border-collapse">
                              <thead>
                                <tr className="text-gray-500 border-b border-amber-100">
                                  <th className="text-left pb-1.5 pr-4 font-medium">Product ID</th>
                                  <th className="text-left pb-1.5 pr-4 font-medium">Product</th>
                                  <th className="text-left pb-1.5 pr-4 font-medium">Brand</th>
                                  <th className="text-right pb-1.5 pr-4 font-medium">Qty</th>
                                  <th className="text-right pb-1.5 pr-4 font-medium">Rate</th>
                                  <th className="text-right pb-1.5 pr-4 font-medium">MRP</th>
                                  <th className="text-left pb-1.5 font-medium">HSN</th>
                                  <th className="text-right pb-1.5 font-medium">GST %</th>
                                </tr>
                              </thead>
                              <tbody>
                                {po.items.map((item, idx) => (
                                  <tr key={idx} className="border-b border-amber-50 last:border-0">
                                    <td className="py-1.5 pr-4 font-mono text-amber-700">{item.productId}</td>
                                    <td className="py-1.5 pr-4 text-gray-700 max-w-[200px] truncate">{item.productName}</td>
                                    <td className="py-1.5 pr-4 text-gray-500">{item.brandName ?? "—"}</td>
                                    <td className="py-1.5 pr-4 text-right font-semibold">{item.requestedQty}</td>
                                    <td className="py-1.5 pr-4 text-right text-gray-600">₹{item.rate}</td>
                                    <td className="py-1.5 pr-4 text-right text-gray-600">₹{item.mrp}</td>
                                    <td className="py-1.5 font-mono text-gray-400">{item.hsnCode ?? "—"}</td>
                                    <td className="py-1.5 text-right text-gray-500">
                                      {item.gstRate != null ? `${item.gstRate}%` : "—"}
                                    </td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          )}
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        )}
      </div>

      {/* Create ASN Modal */}
      {asnModal && (
        <div
          className="fixed inset-0 bg-black/50 z-50 flex items-start justify-center p-4 overflow-y-auto"
          onClick={() => !asnSubmitting && setAsnModal(null)}
        >
          <div
            className="bg-white rounded-xl w-full max-w-2xl shadow-2xl my-8"
            onClick={e => e.stopPropagation()}
          >
            <div className="flex items-center justify-between p-5 border-b">
              <div>
                <h3 className="font-bold text-gray-800">
                  Create ASN —{" "}
                  <span className="font-mono text-amber-700">{asnModal.purchaseOrderId}</span>
                </h3>
                <p className="text-xs text-gray-500 mt-0.5">
                  {asnModal.warehouseName ?? asnModal.warehouseCode ?? ""} · Vendor 18309
                </p>
              </div>
              {!asnSubmitting && (
                <button onClick={() => setAsnModal(null)} className="text-gray-400 hover:text-gray-700 text-lg">✕</button>
              )}
            </div>

            {asnResult ? (
              <div className="p-10 text-center">
                <CheckCircle size={48} className="mx-auto mb-4 text-green-500" />
                <h4 className="font-bold text-gray-800 text-lg mb-1">ASN Submitted Successfully</h4>
                <p className="text-gray-500 text-sm mb-3">Blinkit ASN ID</p>
                <div className="inline-block bg-green-50 border border-green-200 rounded-lg px-5 py-3 font-mono text-green-800 font-bold text-xl mb-5">
                  {asnResult}
                </div>
                <p className="text-xs text-gray-400 mb-6">
                  Save this ASN ID — needed to cancel or reference this shipment.
                </p>
                <button
                  onClick={() => setAsnModal(null)}
                  className="px-6 py-2 bg-amber-500 text-white rounded-lg text-sm hover:bg-amber-600 transition"
                >
                  Done
                </button>
              </div>
            ) : (
              <form onSubmit={handleASNSubmit} className="p-5 space-y-5">
                {asnError && (
                  <div className="flex items-start gap-2 bg-red-50 border border-red-200 rounded-lg p-3 text-sm text-red-700">
                    <AlertCircle size={16} className="mt-0.5 flex-shrink-0" />
                    <span className="break-all">{asnError}</span>
                  </div>
                )}

                <div className="grid grid-cols-3 gap-3">
                  <div>
                    <label className="block text-xs font-medium text-gray-600 mb-1">Invoice Number *</label>
                    <input
                      required type="text" placeholder="e.g. INV-2025-001"
                      value={asnForm.invoiceNumber}
                      onChange={e => setAsnForm({ ...asnForm, invoiceNumber: e.target.value })}
                      className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-amber-400"
                    />
                  </div>
                  <div>
                    <label className="block text-xs font-medium text-gray-600 mb-1">Invoice Date *</label>
                    <input
                      required type="date"
                      value={asnForm.invoiceDate}
                      onChange={e => setAsnForm({ ...asnForm, invoiceDate: e.target.value })}
                      className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-amber-400"
                    />
                  </div>
                  <div>
                    <label className="block text-xs font-medium text-gray-600 mb-1">Delivery Date</label>
                    <input
                      type="date"
                      value={asnForm.deliveryDate}
                      onChange={e => setAsnForm({ ...asnForm, deliveryDate: e.target.value })}
                      className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-amber-400"
                    />
                  </div>
                </div>

                {/* Supplier details */}
                <div className="border border-gray-100 rounded-lg p-3 space-y-3">
                  <p className="text-xs font-semibold text-gray-500 uppercase tracking-wider">Supplier (Your) Details</p>
                  <div className="grid grid-cols-2 gap-3">
                    <div>
                      <label className="block text-xs font-medium text-gray-600 mb-1">Supplier GSTIN *</label>
                      <input
                        required type="text" placeholder="e.g. 27AABCT1332L1ZS"
                        value={asnForm.supplierGstin}
                        onChange={e => setAsnForm({ ...asnForm, supplierGstin: e.target.value.toUpperCase() })}
                        className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-amber-400"
                      />
                    </div>
                    <div>
                      <label className="block text-xs font-medium text-gray-600 mb-1">Address Line 1 *</label>
                      <input
                        required type="text" placeholder="Street / Building"
                        value={asnForm.supplierAddress1}
                        onChange={e => setAsnForm({ ...asnForm, supplierAddress1: e.target.value })}
                        className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-amber-400"
                      />
                    </div>
                  </div>
                  <div className="grid grid-cols-3 gap-3">
                    <div>
                      <label className="block text-xs font-medium text-gray-600 mb-1">City *</label>
                      <input
                        required type="text" placeholder="Mumbai"
                        value={asnForm.supplierCity}
                        onChange={e => setAsnForm({ ...asnForm, supplierCity: e.target.value })}
                        className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-amber-400"
                      />
                    </div>
                    <div>
                      <label className="block text-xs font-medium text-gray-600 mb-1">State *</label>
                      <input
                        required type="text" placeholder="Maharashtra"
                        value={asnForm.supplierState}
                        onChange={e => setAsnForm({ ...asnForm, supplierState: e.target.value })}
                        className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-amber-400"
                      />
                    </div>
                    <div>
                      <label className="block text-xs font-medium text-gray-600 mb-1">Postal Code *</label>
                      <input
                        required type="text" placeholder="400001"
                        value={asnForm.supplierPostalCode}
                        onChange={e => setAsnForm({ ...asnForm, supplierPostalCode: e.target.value })}
                        className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-amber-400"
                      />
                    </div>
                  </div>
                </div>

                {/* Buyer + shipment */}
                <div className="border border-gray-100 rounded-lg p-3 space-y-3">
                  <p className="text-xs font-semibold text-gray-500 uppercase tracking-wider">Shipment Details</p>
                  <div className="grid grid-cols-2 gap-3">
                    <div>
                      <label className="block text-xs font-medium text-gray-600 mb-1">Buyer GSTIN *</label>
                      <input
                        required type="text" placeholder="Auto-filled from PO"
                        value={asnForm.buyerGstin}
                        onChange={e => setAsnForm({ ...asnForm, buyerGstin: e.target.value.toUpperCase() })}
                        className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-amber-400"
                      />
                    </div>
                    <div>
                      <label className="block text-xs font-medium text-gray-600 mb-1">Delivery Type *</label>
                      <select
                        value={asnForm.deliveryType}
                        onChange={e => setAsnForm({ ...asnForm, deliveryType: e.target.value })}
                        className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-amber-400"
                      >
                        <option value="SELF">SELF</option>
                        <option value="COURIER">COURIER</option>
                      </select>
                    </div>
                  </div>
                  {asnForm.deliveryType === "COURIER" && (
                    <div className="grid grid-cols-2 gap-3">
                      <div>
                        <label className="block text-xs font-medium text-gray-600 mb-1">Delivery Partner *</label>
                        <input
                          required type="text" placeholder="e.g. BlueDart, Delhivery"
                          value={asnForm.deliveryPartner}
                          onChange={e => setAsnForm({ ...asnForm, deliveryPartner: e.target.value })}
                          className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-amber-400"
                        />
                      </div>
                      <div>
                        <label className="block text-xs font-medium text-gray-600 mb-1">Tracking Code *</label>
                        <input
                          required type="text" placeholder="Courier tracking number"
                          value={asnForm.trackingCode}
                          onChange={e => setAsnForm({ ...asnForm, trackingCode: e.target.value })}
                          className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-amber-400"
                        />
                      </div>
                    </div>
                  )}
                  <div className="grid grid-cols-3 gap-3">
                    <div>
                      <label className="block text-xs font-medium text-gray-600 mb-1">E-Way Bill No.</label>
                      <input
                        type="text" placeholder="Optional"
                        value={asnForm.eWayBill}
                        onChange={e => setAsnForm({ ...asnForm, eWayBill: e.target.value })}
                        className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-amber-400"
                      />
                    </div>
                    <div>
                      <label className="block text-xs font-medium text-gray-600 mb-1">Vehicle / Licence No.</label>
                      <input
                        type="text" placeholder="e.g. MH-04-AB-1234"
                        value={asnForm.licenseNumber}
                        onChange={e => setAsnForm({ ...asnForm, licenseNumber: e.target.value })}
                        className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-amber-400"
                      />
                    </div>
                    <div>
                      <label className="block text-xs font-medium text-gray-600 mb-1">Driver Phone</label>
                      <input
                        type="text" placeholder="e.g. 9876543210"
                        value={asnForm.driverPhone}
                        onChange={e => setAsnForm({ ...asnForm, driverPhone: e.target.value })}
                        className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-amber-400"
                      />
                    </div>
                  </div>
                </div>

                {/* Existing ASNs */}
                {asnExistingLoading ? (
                  <div className="flex items-center gap-2 text-xs text-gray-400 py-1">
                    <Loader2 size={12} className="animate-spin" /> Loading existing ASNs…
                  </div>
                ) : asnExisting.length > 0 && (
                  <div className="border border-green-100 rounded-lg overflow-hidden">
                    <div className="bg-green-50 px-3 py-2 flex items-center justify-between">
                      <p className="text-xs font-semibold text-green-700 uppercase tracking-wide">
                        Existing ASNs ({asnExisting.length})
                      </p>
                      <p className="text-xs text-green-600">
                        {Object.values(asnAllocations).reduce((s, v) => s + v, 0)} of{" "}
                        {asnModal?.totalQty ?? 0} units invoiced
                      </p>
                    </div>
                    <table className="w-full text-xs">
                      <thead className="bg-gray-50 text-gray-500">
                        <tr>
                          <th className="px-3 py-1.5 text-left font-medium">ASN ID</th>
                          <th className="px-3 py-1.5 text-left font-medium">Invoice</th>
                          <th className="px-3 py-1.5 text-right font-medium">Total Qty</th>
                          <th className="px-3 py-1.5 text-left font-medium">Date</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-gray-50">
                        {asnExisting.map(asn => (
                          <tr key={asn.asn_id} className="hover:bg-gray-50">
                            <td className="px-3 py-1.5 font-mono text-amber-700">{asn.asn_id}</td>
                            <td className="px-3 py-1.5 text-gray-600">{asn.invoice_number ?? "—"}</td>
                            <td className="px-3 py-1.5 text-right font-semibold">{asn.total_qty}</td>
                            <td className="px-3 py-1.5 text-gray-400">
                              {asn.created_at
                                ? new Date(asn.created_at).toLocaleDateString("en-IN", {
                                    day: "2-digit", month: "short",
                                  })
                                : "—"}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}

                {/* Line Items */}
                <div>
                  <div className="flex items-center justify-between mb-2">
                    <label className="text-xs font-semibold text-gray-700 uppercase tracking-wider">
                      Line Items
                    </label>
                    <span className="text-xs text-gray-400">
                      {asnForm.lines.length} items · quantities in units (PC)
                    </span>
                  </div>

                  {asnForm.lines.length === 0 ? (
                    <div className="bg-amber-50 border border-amber-200 rounded-lg p-3 text-xs text-amber-700">
                      No line items. Expand the PO row to load items first, then reopen this modal.
                    </div>
                  ) : (
                    <div className="border border-gray-100 rounded-lg overflow-hidden max-h-72 overflow-y-auto">
                      <table className="w-full text-xs">
                        <thead className="bg-gray-50 text-gray-500 sticky top-0 z-10">
                          <tr>
                            <th className="px-3 py-2 text-left">Product</th>
                            <th className="px-3 py-2 text-right">Ordered</th>
                            <th className="px-3 py-2 text-center w-16">Inv. Qty *</th>
                            <th className="px-3 py-2 text-left w-24">UPC</th>
                            <th className="px-3 py-2 text-left w-20">Batch *</th>
                            <th className="px-3 py-2 text-left w-28">Mfg Date</th>
                            <th className="px-3 py-2 text-left w-28">Expiry Date</th>
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-gray-50">
                          {asnForm.lines.map((line, idx) => (
                            <tr key={idx} className="hover:bg-gray-50">
                              <td className="px-3 py-2">
                                <div className="font-mono text-amber-700 text-xs">{line.productId}</div>
                                <div className="text-gray-500 truncate max-w-[150px] text-xs">{line.productName}</div>
                                {line.hsnCode && <div className="text-gray-400 text-xs font-mono">HSN: {line.hsnCode}</div>}
                              </td>
                              <td className="px-3 py-2 text-right text-gray-500 text-xs">{line.requestedQty}</td>
                              <td className="px-3 py-2">
                                <input
                                  required type="number" min="0" max={line.requestedQty}
                                  value={line.invoicedQty}
                                  onChange={e => {
                                    const l = [...asnForm.lines];
                                    l[idx] = { ...l[idx], invoicedQty: Number(e.target.value) };
                                    setAsnForm({ ...asnForm, lines: l });
                                  }}
                                  className="w-full border border-gray-200 rounded px-2 py-1 text-center text-xs focus:outline-none focus:ring-1 focus:ring-amber-400"
                                />
                              </td>
                              <td className="px-3 py-2">
                                <input
                                  type="text" placeholder="Barcode"
                                  value={line.upc}
                                  onChange={e => {
                                    const l = [...asnForm.lines];
                                    l[idx] = { ...l[idx], upc: e.target.value };
                                    setAsnForm({ ...asnForm, lines: l });
                                  }}
                                  className="w-full border border-gray-200 rounded px-2 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-amber-400"
                                />
                              </td>
                              <td className="px-3 py-2">
                                <input
                                  required type="text" placeholder="Batch #"
                                  value={line.batchNumber}
                                  onChange={e => {
                                    const l = [...asnForm.lines];
                                    l[idx] = { ...l[idx], batchNumber: e.target.value };
                                    setAsnForm({ ...asnForm, lines: l });
                                  }}
                                  className="w-full border border-gray-200 rounded px-2 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-amber-400"
                                />
                              </td>
                              <td className="px-3 py-2">
                                <input
                                  type="date"
                                  value={line.mfgDate}
                                  onChange={e => {
                                    const l = [...asnForm.lines];
                                    l[idx] = { ...l[idx], mfgDate: e.target.value };
                                    setAsnForm({ ...asnForm, lines: l });
                                  }}
                                  className="w-full border border-gray-200 rounded px-2 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-amber-400"
                                />
                              </td>
                              <td className="px-3 py-2">
                                <input
                                  type="date"
                                  value={line.expiryDate}
                                  onChange={e => {
                                    const l = [...asnForm.lines];
                                    l[idx] = { ...l[idx], expiryDate: e.target.value };
                                    setAsnForm({ ...asnForm, lines: l });
                                  }}
                                  className="w-full border border-gray-200 rounded px-2 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-amber-400"
                                />
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>

                <div className="flex gap-3 pt-2 border-t border-gray-100">
                  <button
                    type="button"
                    onClick={() => setAsnModal(null)}
                    disabled={asnSubmitting}
                    className="flex-1 px-4 py-2 border border-gray-200 rounded-lg text-sm hover:bg-gray-50 disabled:opacity-50 transition"
                  >
                    Cancel
                  </button>
                  <button
                    type="submit"
                    disabled={asnSubmitting || asnForm.lines.every(l => l.invoicedQty === 0)}
                    className="flex-1 px-4 py-2 bg-amber-500 text-white rounded-lg text-sm hover:bg-amber-600 disabled:opacity-50 transition"
                  >
                    {asnSubmitting
                      ? <span className="flex items-center justify-center gap-2"><Loader2 size={14} className="animate-spin" /> Submitting…</span>
                      : "Submit ASN to Blinkit"}
                  </button>
                </div>
              </form>
            )}
          </div>
        </div>
      )}

      {/* PO Amendment Modal */}
      {amendModal && (
        <div
          className="fixed inset-0 bg-black/50 z-50 flex items-start justify-center p-4 overflow-y-auto"
          onClick={() => !amendSubmitting && setAmendModal(null)}
        >
          <div
            className="bg-white rounded-xl w-full max-w-2xl shadow-2xl my-8"
            onClick={e => e.stopPropagation()}
          >
            <div className="flex items-center justify-between p-5 border-b">
              <div>
                <h3 className="font-bold text-gray-800">
                  Request PO Amendment —{" "}
                  <span className="font-mono text-blue-700">{amendModal.purchaseOrderId}</span>
                </h3>
                <p className="text-xs text-gray-500 mt-0.5">
                  Correct MRP, UPC, or Unit of Measure for items on this PO
                </p>
              </div>
              {!amendSubmitting && (
                <button onClick={() => setAmendModal(null)} className="text-gray-400 hover:text-gray-700 text-lg">✕</button>
              )}
            </div>

            {amendResult?.success ? (
              <div className="p-8 text-center">
                <CheckCircle size={48} className="mx-auto mb-4 text-green-500" />
                <h4 className="font-bold text-gray-800 text-lg mb-1">Amendment Submitted</h4>
                <p className="text-gray-500 text-sm mb-4">{amendResult.message ?? "Blinkit has received the correction request."}</p>
                {amendResult.updated_items && amendResult.updated_items.length > 0 && (
                  <div className="border border-gray-100 rounded-lg overflow-hidden mb-5 text-left">
                    <div className="bg-green-50 px-4 py-2">
                      <p className="text-xs font-semibold text-green-700 uppercase tracking-wide">Updated Items</p>
                    </div>
                    <table className="w-full text-xs">
                      <thead className="bg-gray-50 text-gray-500">
                        <tr>
                          <th className="px-3 py-2 text-left">Item ID</th>
                          <th className="px-3 py-2 text-right">Cost Price</th>
                          <th className="px-3 py-2 text-right">Landing Price</th>
                          <th className="px-3 py-2 text-right">Tax</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-gray-50">
                        {amendResult.updated_items.map((item: any, i: number) => (
                          <tr key={i} className="hover:bg-gray-50">
                            <td className="px-3 py-1.5 font-mono text-blue-700">{item.item_id ?? item.itemId ?? "—"}</td>
                            <td className="px-3 py-1.5 text-right">₹{item.cost_price ?? item.costPrice ?? "—"}</td>
                            <td className="px-3 py-1.5 text-right">₹{item.landing_price ?? item.landingPrice ?? "—"}</td>
                            <td className="px-3 py-1.5 text-right">{item.tax ?? "—"}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
                <button
                  onClick={() => setAmendModal(null)}
                  className="px-6 py-2 bg-blue-600 text-white rounded-lg text-sm hover:bg-blue-700 transition"
                >
                  Done
                </button>
              </div>
            ) : (
              <form onSubmit={handleAmendSubmit} className="p-5 space-y-4">
                {/* Info banner */}
                <div className="bg-blue-50 border border-blue-200 rounded-lg p-3 text-xs text-blue-800 space-y-1">
                  <p><strong>Endpoint:</strong> <code className="bg-white border border-blue-200 px-1 rounded">POST /webhook/public/v1/po/amendment</code></p>
                  <p>Select items to correct their MRP, UPC, or Unit of Measure. Blinkit will recalculate cost price, landing price, and tax.</p>
                  <p className="text-amber-700"><strong>⚠ Test environment:</strong> Blinkit may return 404 if the amendment endpoint isn't activated for Vendor 18309 on dev.partnersbiz.com — contact Blinkit to enable it.</p>
                </div>

                {amendError && (
                  <div className="flex items-start gap-2 bg-red-50 border border-red-200 rounded-lg p-3 text-sm text-red-700">
                    <AlertCircle size={16} className="mt-0.5 flex-shrink-0" />
                    <span className="break-all">{amendError}</span>
                  </div>
                )}

                {amendLines.length === 0 ? (
                  <div className="bg-amber-50 border border-amber-200 rounded-lg p-3 text-xs text-amber-700">
                    No items found. Expand the PO row to load items first, then reopen this modal.
                  </div>
                ) : (
                  <div className="border border-gray-100 rounded-lg overflow-hidden max-h-80 overflow-y-auto">
                    <table className="w-full text-xs">
                      <thead className="bg-gray-50 text-gray-500 sticky top-0 z-10">
                        <tr>
                          <th className="px-3 py-2 text-center w-8">✓</th>
                          <th className="px-3 py-2 text-left">Item</th>
                          <th className="px-3 py-2 text-center w-20">MRP (₹)</th>
                          <th className="px-3 py-2 text-left w-24">UPC</th>
                          <th className="px-3 py-2 text-center w-16">UOM Type</th>
                          <th className="px-3 py-2 text-center w-16">Value</th>
                          <th className="px-3 py-2 text-center w-14">Unit</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-gray-50">
                        {amendLines.map((line, idx) => (
                          <tr key={idx} className={`hover:bg-gray-50 ${line.include ? "bg-blue-50/40" : ""}`}>
                            <td className="px-3 py-2 text-center">
                              <input
                                type="checkbox"
                                checked={line.include}
                                onChange={e => {
                                  const l = [...amendLines];
                                  l[idx] = { ...l[idx], include: e.target.checked };
                                  setAmendLines(l);
                                }}
                                className="w-4 h-4 accent-blue-600"
                              />
                            </td>
                            <td className="px-3 py-2">
                              <div className="font-mono text-blue-700">{line.item_id}</div>
                              <div className="text-gray-500 truncate max-w-[150px]">{line.productName}</div>
                            </td>
                            <td className="px-3 py-2">
                              <input
                                type="number" min="0" step="0.01"
                                value={line.mrp}
                                disabled={!line.include}
                                onChange={e => {
                                  const l = [...amendLines];
                                  l[idx] = { ...l[idx], mrp: Number(e.target.value) };
                                  setAmendLines(l);
                                }}
                                className="w-full border border-gray-200 rounded px-2 py-1 text-center text-xs focus:outline-none focus:ring-1 focus:ring-blue-400 disabled:bg-gray-50 disabled:text-gray-400"
                              />
                            </td>
                            <td className="px-3 py-2">
                              <input
                                type="text" placeholder="Barcode"
                                value={line.upc}
                                disabled={!line.include}
                                onChange={e => {
                                  const l = [...amendLines];
                                  l[idx] = { ...l[idx], upc: e.target.value };
                                  setAmendLines(l);
                                }}
                                className="w-full border border-gray-200 rounded px-2 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-blue-400 disabled:bg-gray-50 disabled:text-gray-400"
                              />
                            </td>
                            <td className="px-3 py-2">
                              <select
                                value={line.uom_type}
                                disabled={!line.include}
                                onChange={e => {
                                  const l = [...amendLines];
                                  l[idx] = { ...l[idx], uom_type: e.target.value };
                                  setAmendLines(l);
                                }}
                                className="w-full border border-gray-200 rounded px-1 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-blue-400 disabled:bg-gray-50 disabled:text-gray-400"
                              >
                                <option value="STANDARD">STD</option>
                                <option value="CUSTOM">CUSTOM</option>
                              </select>
                            </td>
                            <td className="px-3 py-2">
                              <input
                                type="text" placeholder="250"
                                value={line.uom_value}
                                disabled={!line.include}
                                onChange={e => {
                                  const l = [...amendLines];
                                  l[idx] = { ...l[idx], uom_value: e.target.value };
                                  setAmendLines(l);
                                }}
                                className="w-full border border-gray-200 rounded px-2 py-1 text-center text-xs focus:outline-none focus:ring-1 focus:ring-blue-400 disabled:bg-gray-50 disabled:text-gray-400"
                              />
                            </td>
                            <td className="px-3 py-2">
                              <input
                                type="text" placeholder="g"
                                value={line.uom_unit}
                                disabled={!line.include}
                                onChange={e => {
                                  const l = [...amendLines];
                                  l[idx] = { ...l[idx], uom_unit: e.target.value };
                                  setAmendLines(l);
                                }}
                                className="w-full border border-gray-200 rounded px-2 py-1 text-center text-xs focus:outline-none focus:ring-1 focus:ring-blue-400 disabled:bg-gray-50 disabled:text-gray-400"
                              />
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}

                <p className="text-xs text-gray-400">
                  {amendLines.filter(l => l.include).length} of {amendLines.length} items selected for amendment
                </p>

                <div className="flex gap-3 pt-2 border-t border-gray-100">
                  <button
                    type="button"
                    onClick={() => setAmendModal(null)}
                    disabled={amendSubmitting}
                    className="flex-1 px-4 py-2 border border-gray-200 rounded-lg text-sm hover:bg-gray-50 disabled:opacity-50 transition"
                  >
                    Cancel
                  </button>
                  <button
                    type="submit"
                    disabled={amendSubmitting || !amendLines.some(l => l.include)}
                    className="flex-1 px-4 py-2 bg-blue-600 text-white rounded-lg text-sm hover:bg-blue-700 disabled:opacity-50 transition"
                  >
                    {amendSubmitting
                      ? <span className="flex items-center justify-center gap-2"><Loader2 size={14} className="animate-spin" /> Submitting…</span>
                      : "Submit Amendment to Blinkit"}
                  </button>
                </div>
              </form>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
