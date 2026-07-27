import axios from "axios";

const BASE_URL = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "http://localhost:8000";

export const apiClient = axios.create({
  baseURL: BASE_URL,
  headers: { "Content-Type": "application/json" },
  withCredentials: true,
});

// Same basename Vite bakes into index.html/router.tsx (e.g. "/edi-frontend"
// when reverse-proxied under a subpath, "" for root deploys) — needed here
// too since window.location.href needs a real path, not a router-relative one.
const BASENAME = import.meta.env.BASE_URL.replace(/\/$/, "");

// 401 → clear cached user and redirect to login (unless already there,
// which would cause an infinite reload loop)
apiClient.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      localStorage.removeItem("edi_user");
      const loginPath = `${BASENAME}/login`;
      if (window.location.pathname !== loginPath) {
        window.location.href = loginPath;
      }
    }
    return Promise.reject(error);
  },
);

export default apiClient;
