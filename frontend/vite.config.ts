import path from "path";
import { loadEnv } from "vite";
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  // This file runs in Node before Vite populates import.meta.env, so `process.env`
  // sees only real shell variables — not anything in .env / .env.local. loadEnv is
  // what actually reads those files. The "" prefix loads every key, not just VITE_*.
  const env = loadEnv(mode, __dirname, "");
  const proxyTarget = env.VITE_PROXY_TARGET || "http://localhost:8001";

  return {
  base: env.VITE_BASE_PATH || "/",
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    port: 5173,
    // Proxy the API through the dev server so the browser only ever makes
    // same-origin requests. This is not just convenience: the SPA authenticates
    // with an httpOnly cookie set `SameSite=Lax` (app/api/routes/auth.py), which
    // browsers refuse to send on cross-site XHR. Pointing VITE_API_BASE_URL
    // straight at the deployed host therefore logs in and then 401s on every
    // subsequent request. Proxying keeps the origin as localhost:5173, so the
    // cookie is sent normally and no CORS entry is needed for the dev machine.
    //
    // VITE_PROXY_TARGET (frontend/.env.local) selects the backend:
    //   unset -> http://localhost:8001            (local Docker stack; the api
    //            container publishes on 8001, see API_HOST_PORT in docker-compose.yml)
    //   set   -> https://www.letstryfoods.com/edi-backend
    proxy: Object.fromEntries(
      ["/api", "/auth", "/health"].map((prefix) => [
        prefix,
        {
          target: proxyTarget,
          changeOrigin: true,
          // Re-scope Set-Cookie from the deployed domain to localhost, or the
          // browser drops the session cookie as foreign.
          cookieDomainRewrite: "localhost",
        },
      ]),
    ),
  },
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    css: false,
    pool: "threads",
    fileParallelism: false,
  },
  };
});
