import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev proxy: the React app calls /api/* and Vite forwards it to the FastAPI
// backend on :8000, so there is no CORS to configure and the bearer token can
// be sent as a normal Authorization header (no EventSource — we poll /state).
export default defineConfig({
  plugins: [react()],
  base: "/kube-verdict/",
  server: {
    proxy: {
      "/api": {
        target: process.env.KV_API_URL || "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
});
