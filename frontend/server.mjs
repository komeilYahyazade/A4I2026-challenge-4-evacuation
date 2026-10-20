// readiness-web runtime: serves the built SPA and proxies /api, /apps and /run_sse to the PRIVATE
// readiness-agent with a Google ID token from the Cloud Run metadata server (audience = AGENT_UPSTREAM).
// Zero dependencies. SSE is streamed through unbuffered.
import { createReadStream, existsSync, statSync } from "node:fs";
import { createServer } from "node:http";
import { extname, join, normalize } from "node:path";
import { Readable } from "node:stream";

const PORT = Number(process.env.PORT || 8080);
const UPSTREAM = (process.env.AGENT_UPSTREAM || "").replace(/\/$/, "");
const DIST = join(process.cwd(), "dist");
const PROXIED = [/^\/api(\/|$)/, /^\/apps(\/|$)/, /^\/run_sse$/];
const TYPES = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".ico": "image/x-icon",
  ".woff2": "font/woff2",
};

let cached = { token: "", exp: 0 };
async function idToken() {
  if (cached.token && Date.now() < cached.exp) return cached.token;
  const url = `http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/identity?audience=${encodeURIComponent(UPSTREAM)}&format=full`;
  const res = await fetch(url, { headers: { "Metadata-Flavor": "Google" } });
  if (!res.ok) throw new Error(`metadata identity ${res.status}`);
  const token = (await res.text()).trim();
  cached = { token, exp: Date.now() + 50 * 60 * 1000 };
  return token;
}

function configJs() {
  const cfg = {
    MODE: process.env.MODE || "",
    AGENT_URL: "",
    APP: process.env.APP || "agent",
    MAPS_API_KEY: process.env.MAPS_API_KEY || "",
    MAP_ID: process.env.MAP_ID || "DEMO_MAP_ID",
  };
  return `window.__READINESS_CONFIG__ = ${JSON.stringify(cfg)};\n`;
}

async function proxy(req, res) {
  if (!UPSTREAM) {
    res.writeHead(503, { "content-type": "application/json" });
    return res.end(JSON.stringify({ error: "NO_UPSTREAM", detail: "AGENT_UPSTREAM is not set" }));
  }
  try {
    const local = /^http:\/\/(localhost|127\.0\.0\.1)/.test(UPSTREAM);
    const headers = local ? {} : { authorization: `Bearer ${await idToken()}` };
    for (const h of ["content-type", "accept", "last-event-id"]) if (req.headers[h]) headers[h] = req.headers[h];
    const hasBody = !["GET", "HEAD"].includes(req.method);
    const upstream = await fetch(UPSTREAM + req.url, {
      method: req.method,
      headers,
      body: hasBody ? Readable.toWeb(req) : undefined,
      duplex: hasBody ? "half" : undefined,
    });
    const out = { "cache-control": "no-store" };
    for (const h of ["content-type"]) {
      const v = upstream.headers.get(h);
      if (v) out[h] = v;
    }
    if ((out["content-type"] || "").includes("text/event-stream")) out["x-accel-buffering"] = "no";
    res.writeHead(upstream.status, out);
    if (!upstream.body) return res.end();
    Readable.fromWeb(upstream.body).pipe(res);
  } catch (e) {
    res.writeHead(502, { "content-type": "application/json" });
    res.end(JSON.stringify({ error: "PROXY_ERROR", detail: String(e && e.message ? e.message : e) }));
  }
}

function serveStatic(req, res) {
  const path = decodeURIComponent((req.url || "/").split("?")[0]);
  let file = normalize(join(DIST, path));
  if (!file.startsWith(DIST)) {
    res.writeHead(403);
    return res.end();
  }
  if (!existsSync(file) || statSync(file).isDirectory()) file = join(DIST, "index.html");
  const ext = extname(file);
  res.writeHead(200, {
    "content-type": TYPES[ext] || "application/octet-stream",
    "cache-control": path.startsWith("/assets/") ? "public, max-age=31536000, immutable" : "no-cache",
  });
  createReadStream(file).pipe(res);
}

createServer((req, res) => {
  const path = (req.url || "/").split("?")[0];
  if (path === "/config.js") {
    res.writeHead(200, { "content-type": "text/javascript; charset=utf-8", "cache-control": "no-store" });
    return res.end(configJs());
  }
  if (path === "/web-health") {
    res.writeHead(200, { "content-type": "application/json" });
    return res.end(JSON.stringify({ ok: true, upstream: !!UPSTREAM }));
  }
  if (PROXIED.some((r) => r.test(path))) return void proxy(req, res);
  serveStatic(req, res);
}).listen(PORT, () => console.log(`readiness-web on :${PORT} → ${UPSTREAM || "(no upstream)"}`));
