import react from "@vitejs/plugin-react";
import { execSync } from "node:child_process";
import { defineConfig, loadEnv, type ProxyOptions } from "vite";

// Dev proxy for the private agent. The UI calls same-origin /api, /apps and /run_sse; this forwards
// them to VITE_AGENT_PROXY_TARGET. For an https (Cloud Run) target it adds an ID token from
// `gcloud auth print-identity-token`, refreshed every 45 minutes. http://localhost:8000 needs none.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const target = env.VITE_AGENT_PROXY_TARGET || "http://localhost:8000";
  let token = "";
  const refresh = () => {
    if (!target.startsWith("https://")) return;
    try {
      token = execSync("gcloud auth print-identity-token", { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] }).trim();
      console.log(`[proxy] ID token refreshed for ${target}`);
    } catch {
      console.warn("[proxy] gcloud auth print-identity-token failed; calls to the private agent will 403");
    }
  };
  refresh();
  setInterval(refresh, 45 * 60 * 1000).unref();
  const proxy: ProxyOptions = {
    target,
    changeOrigin: true,
    configure: (p) => {
      p.on("proxyReq", (req) => {
        if (token) req.setHeader("Authorization", `Bearer ${token}`);
      });
    },
  };
  return {
    plugins: [react()],
    server: {
      host: "127.0.0.1",
      port: 5173,
      proxy: { "/api": proxy, "/apps": proxy, "/run_sse": proxy },
    },
    build: { outDir: "dist", sourcemap: false },
  };
});
