import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";

export default defineConfig(({ mode }) => {
  // Load environment variables from frontend directory
  const env = loadEnv(mode, process.cwd(), "");

  const port = parseInt(env.PORT || "5173", 10);
  let apiUrl = env.VITE_API_URL || env.API_URL || "http://localhost:8000";

  // Prepend protocol if missing to prevent http-proxy split crash
  if (apiUrl && !apiUrl.startsWith("http://") && !apiUrl.startsWith("https://")) {
    if (apiUrl.startsWith("localhost") || apiUrl.startsWith("127.0.0.1")) {
      apiUrl = `http://${apiUrl}`;
    } else {
      apiUrl = `https://${apiUrl}`;
    }
  }

  return {
    plugins: [react()],
    server: {
      port,
      proxy: {
        "/api": {
          target: apiUrl,
          changeOrigin: true,
          secure: false,
          configure: (proxy, _options) => {
            proxy.on("error", (err, req, _res) => {
              console.warn(
                `[Proxy Warning] Failed to reach backend at ${apiUrl} for ${req.url}: ${err.message}. ` +
                `Ensure your backend server is running and the domain is correct.`
              );
            });
          },
        },
      },
    },
  };
});
