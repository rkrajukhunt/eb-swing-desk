import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";

export default defineConfig(({ mode }) => {
  // Load environment variables from frontend directory
  const env = loadEnv(mode, process.cwd(), "");

  const port = parseInt(env.PORT || "5173", 10);
  const apiUrl = env.VITE_API_URL || env.API_URL || "http://localhost:8000";

  return {
    plugins: [react()],
    server: {
      port,
      proxy: {
        "/api": apiUrl,
      },
    },
  };
});
