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

export const router = createBrowserRouter([
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
      { path: "/pos", element: <POListPage /> },
      { path: "/pos/:poId", element: <PODetailPage /> },
      { path: "/exceptions", element: <ExceptionsPage /> },
      { path: "/master-data", element: <MasterDataPage /> },
      { path: "/b1-logs", element: <B1LogsPage /> },
    ],
  },
]);
