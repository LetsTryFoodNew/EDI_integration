import { BrowserRouter, Routes, Route } from "react-router-dom";
import Sidebar from "./components/Sidebar";
import Dashboard from "./pages/Dashboard";
import PurchaseOrders from "./pages/PurchaseOrders";
import PODetail from "./pages/PODetail";
import CreatePO from "./pages/CreatePO";
import Inventory from "./pages/Inventory";
import Companies from "./pages/Companies";
import WebhookLogs from "./pages/WebhookLogs";
import ASNPage from "./pages/ASNPage";
import IntegrationSetup from "./pages/IntegrationSetup";
import ProductMappings from "./pages/ProductMappings";
import SAPOrders from "./pages/SAPOrders";
import UnmappedSKUs from "./pages/UnmappedSKUs";
import ZeptoPOs from "./pages/ZeptoPOs";
import ZeptoASN from "./pages/ZeptoASN";
import BlinkitPOs from "./pages/BlinkitPOs";
import BlinkitASN from "./pages/BlinkitASN";
import ApiDoc from "./pages/ApiDoc";

function App() {
  return (
    <BrowserRouter>
      <div className="flex min-h-screen bg-gray-50">
        <Sidebar />
        <main className="flex-1 ml-64 min-h-screen p-8 overflow-y-auto bg-gray-50">
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/purchase-orders" element={<PurchaseOrders />} />
            <Route path="/purchase-orders/new" element={<CreatePO />} />
            <Route path="/purchase-orders/:id" element={<PODetail />} />
            <Route path="/products" element={<Inventory />} />
            <Route path="/companies" element={<Companies />} />
            <Route path="/webhooks" element={<WebhookLogs />} />
            <Route path="/asn" element={<ASNPage />} />
            <Route path="/integration" element={<IntegrationSetup />} />
            <Route path="/product-mappings" element={<ProductMappings />} />
            <Route path="/sap-orders" element={<SAPOrders />} />
            <Route path="/unmapped-skus" element={<UnmappedSKUs />} />
            <Route path="/zepto/pos" element={<ZeptoPOs />} />
            <Route path="/zepto/asn" element={<ZeptoASN />} />
            <Route path="/blinkit/pos" element={<BlinkitPOs />} />
            <Route path="/blinkit/asn" element={<BlinkitASN />} />
            <Route path="/api-doc" element={<ApiDoc />} />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  );
}

export default App;

