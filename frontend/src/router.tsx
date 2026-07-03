import { createBrowserRouter } from "react-router-dom";
import HomePage from "@/features/dashboard/HomePage";
import LoginPage from "@/pages/LoginPage";
import PlaceholderPage from "@/pages/PlaceholderPage";

export const router = createBrowserRouter([
  { path: "/login", element: <LoginPage /> },
  { path: "/", element: <HomePage /> },
  { path: "/pos", element: <PlaceholderPage title="Purchase Orders" /> },
  { path: "/exceptions", element: <PlaceholderPage title="Exceptions" /> },
  { path: "/master-data", element: <PlaceholderPage title="Master Data" /> },
]);
