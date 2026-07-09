import dns from "dns";
import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";

// Force Node to use Google and Cloudflare DNS to bypass local ISP DNS caching issues
dns.setServers(["8.8.8.8", "1.1.1.1"]);

export default defineConfig(async ({ mode }) => {
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
  } else if (apiUrl && apiUrl.startsWith("http://")) {
    // If it's a remote URL starting with http://, force https:// to avoid redirects and CORS issues on Railway
    try {
      const parsedUrl = new URL(apiUrl);
      if (parsedUrl.hostname !== "localhost" && parsedUrl.hostname !== "127.0.0.1") {
        apiUrl = apiUrl.replace("http://", "https://");
      }
    } catch (e) {
      // Ignore URL parsing errors here
    }
  }

  let proxyTarget = apiUrl;
  const customHeaders: Record<string, string> = {};

  try {
    const urlObj = new URL(apiUrl);
    const hostname = urlObj.hostname;
    
    // Only resolve remote hosts, leave localhost/loopback alone
    if (hostname !== "localhost" && hostname !== "127.0.0.1") {
      const addresses = await dns.promises.resolve4(hostname);
      if (addresses && addresses.length > 0) {
        proxyTarget = `${urlObj.protocol}//${addresses[0]}`;
        customHeaders["host"] = urlObj.host;
        console.log(`[Vite Proxy] Resolved ${hostname} to ${addresses[0]} via Google DNS`);
      }
    }
  } catch (err: any) {
    console.warn(`[Vite Proxy Warning] DNS resolution failed for proxy target: ${err.message}. Using default.`);
  }

  return {
    plugins: [react()],
    server: {
      port,
      proxy: {
        "/api": {
          target: proxyTarget,
          changeOrigin: true,
          secure: false,
          headers: customHeaders,
          configure: (proxy: any, _options: any) => {
            proxy.on("proxyReq", (proxyReq: any, _req: any, _res: any) => {
              try {
                const targetHost = new URL(apiUrl).host;
                proxyReq.setHeader("host", targetHost);
              } catch (e) {
                // Ignore
              }
            });
            proxy.on("error", (err: any, req: any, _res: any) => {
              const reqUrl = (req as any)?.url || "";
              console.warn(
                `[Proxy Warning] Failed to reach backend at ${apiUrl} for ${reqUrl}: ${err.message}. ` +
                `Ensure your backend server is running and the domain is correct.`
              );
            });
          },
        },
      },
    },
  };
});
