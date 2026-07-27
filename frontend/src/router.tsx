import { createBrowserRouter } from "react-router-dom";
import Shell from "@/components/layout/Shell";
import ProtectedRoute from "@/features/auth/ProtectedRoute";
import LoginPage from "@/pages/LoginPage";
import DashboardPage from "@/features/dashboard/DashboardPage";
import POListPage from "@/features/pos/POListPage";
import PODetailPage from "@/features/pos/PODetailPage";
import ExceptionsPage from "@/features/exceptions/ExceptionsPage";
import MasterDataPage from "@/features/master-data/MasterDataPage";
import B1LogsPage from "@/features/b1-logs/B1LogsPage";
import InboxPage from "@/features/inbox/InboxPage";
import InboxDetailPage from "@/features/inbox/InboxDetailPage";
import ApiInboxPage from "@/features/api-inbox/ApiInboxPage";
import ApiInboxDetailPage from "@/features/api-inbox/ApiInboxDetailPage";

// Vite's `base` (VITE_BASE_PATH at build time) drives this automatically, so
// the router's mount path can never drift out of sync with the asset paths
// baked into index.html — e.g. "/edi-frontend/" when reverse-proxied under a
// subpath, "/" for local dev and bare-host deploys.
const basename = import.meta.env.BASE_URL.replace(/\/$/, "") || "/";

export const router = createBrowserRouter(
  [
    { path: "/login", element: <LoginPage /> },
    {
      element: (
        <ProtectedRoute>
          <Shell />
        </ProtectedRoute>
      ),
      children: [
        { path: "/", element: <DashboardPage /> },
        { path: "/inbox", element: <InboxPage /> },
        { path: "/inbox/:messageId", element: <InboxDetailPage /> },
        { path: "/api-inbox", element: <ApiInboxPage /> },
        { path: "/api-inbox/:messageId", element: <ApiInboxDetailPage /> },
        { path: "/pos", element: <POListPage /> },
        { path: "/pos/:poId", element: <PODetailPage /> },
        { path: "/exceptions", element: <ExceptionsPage /> },
        { path: "/master-data", element: <MasterDataPage /> },
        { path: "/b1-logs", element: <B1LogsPage /> },
      ],
    },
  ],
  { basename },
);
