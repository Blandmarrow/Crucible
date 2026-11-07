import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// CRUCIBLE_PORT / CRUCIBLE_VITE_PORT come from the launchers (scripts/bind_host.py
// resolves them from the environment, then .env); the defaults are 8000 and 5173.
const apiPort = process.env.CRUCIBLE_PORT || "8000";
const vitePort = Number(process.env.CRUCIBLE_VITE_PORT) || 5173;

export default defineConfig({
  plugins: [react()],
  server: {
    port: vitePort,
    proxy: {
      "/api": {
        // The launchers export CRUCIBLE_API_TARGET from scripts/bind_host.py: a
        // backend bound to one LAN address does not answer on localhost.
        target: process.env.CRUCIBLE_API_TARGET || `http://localhost:${apiPort}`,
        // Forward the browser's own Host, so the backend's Origin check
        // (backend/request_guard.py) sees Host and Origin agree. Rewriting it to
        // localhost:<backend port> only worked for a browser on this machine, whose
        // http://localhost:<vite port> origin is on the backend's allowlist; `dev --lan`
        // from another device sends Origin http://<lan-ip>:<vite port> and was refused.
        changeOrigin: false,
      },
    },
  },
});
