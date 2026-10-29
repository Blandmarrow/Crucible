import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        // The launchers export CRUCIBLE_API_TARGET from scripts/bind_host.py: a
        // backend bound to one LAN address does not answer on localhost.
        target: process.env.CRUCIBLE_API_TARGET || "http://localhost:8000",
        // Forward the browser's own Host, so the backend's Origin check
        // (backend/request_guard.py) sees Host and Origin agree. Rewriting it to
        // localhost:8000 only worked for a browser on this machine, whose
        // http://localhost:5173 origin is on the backend's allowlist; `dev --lan`
        // from another device sends Origin http://<lan-ip>:5173 and was refused.
        changeOrigin: false,
      },
    },
  },
});
