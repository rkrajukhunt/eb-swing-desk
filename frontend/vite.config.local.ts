// TEMPORARY dev-only config — backend moved to :8001 because :8000 was occupied.
// Not part of the repo; delete when done. Use: npx vite --config vite.config.local.ts
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": "http://localhost:8001",
    },
  },
});
