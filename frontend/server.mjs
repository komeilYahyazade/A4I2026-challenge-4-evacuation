// readiness-web runtime: serves the built SPA and proxies /api, /apps and /run_sse to the PRIVATE
// readiness-agent with a Google ID token from the Cloud Run metadata server (audience = AGENT_UPSTREAM).
// Zero dependencies. SSE is streamed through unbuffered.
import { createReadStream, existsSync, statSync } from "node:fs";
import { createServer, request as httpRequest } from "node:http";
import { request as httpsRequest } from "node:https";
import { extname, join, normalize } from "node:path";
import { Readable } from "node:stream";

const PORT = Number(process.env.PORT || 8080);
const UPSTREAM = (process.env.AGENT_UPSTREAM || "").replace(/\/$/, "");
const DIST = join(process.cwd(), "dist");
const PROXIED = [/^\/api(\/|$)/, /^\/apps(\/|$)/, /^\/run_sse$/];
// PUBLIC_DEMO=1 when the service is reachable without sign-in. Plan drafting and approval stay with
// signed-in users, and only the ADK route the UI uses (session create) is forwarded.
const PUBLIC_DEMO = process.env.PUBLIC_DEMO === "1";
const VOICE_ENABLED = process.env.VOICE_ENABLED === "1";
const VOICE_PUBLIC = process.env.VOICE_PUBLIC === "1";
const VOICE_MAX_MS = 3 * 60_000;
const VOICE_MAX_LIVE = 2;
const VOICE_PER_IP = 3;
const VOICE_WINDOW_MS = 10 * 60_000;
const voiceHits = new Map();
let voiceLive = 0;
const PUBLIC_APPS = /^\/apps\/[^/]+\/users\/[^/]+\/sessions\/?$/;
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
    PUBLIC_DEMO,
    VOICE_ENABLED,
    VOICE_PUBLIC,
  };
  return `window.__READINESS_CONFIG__ = ${JSON.stringify(cfg)};\n`;
}

const MAX_BODY = 64 * 1024;

function sendJson(res, status, body, extra = {}) {
  res.writeHead(status, { "content-type": "application/json", ...extra });
  res.end(JSON.stringify(body));
}

/** Read the request body, refusing anything over MAX_BODY. Returns null after sending a 413. */
function readBody(req, res) {
  return new Promise((resolve) => {
    const declared = Number(req.headers["content-length"] || 0);
    if (declared > MAX_BODY) {
      sendJson(res, 413, { error: "BODY_TOO_LARGE", detail: `Request bodies are capped at ${MAX_BODY / 1024} KB.` });
      req.resume();
      return resolve(null);
    }
    const chunks = [];
    let size = 0;
    let done = false;
    req.on("data", (c) => {
      if (done) return;
      size += c.length;
      if (size > MAX_BODY) {
        done = true;
        sendJson(res, 413, { error: "BODY_TOO_LARGE", detail: `Request bodies are capped at ${MAX_BODY / 1024} KB.` });
        req.resume();
        return resolve(null);
      }
      chunks.push(c);
    });
    req.on("end", () => !done && resolve(Buffer.concat(chunks)));
    req.on("error", () => !done && resolve(null));
  });
}

async function proxy(req, res) {
  if (!UPSTREAM) return sendJson(res, 503, { error: "NO_UPSTREAM", detail: "AGENT_UPSTREAM is not set" });
  try {
    const hasBody = !["GET", "HEAD"].includes(req.method);
    const body = hasBody ? await readBody(req, res) : undefined;
    if (hasBody && body === null) return;
    const local = /^http:\/\/(localhost|127\.0\.0\.1)/.test(UPSTREAM);
    const headers = local ? {} : { authorization: `Bearer ${await idToken()}` };
    // Allowlist only: a client can never set x-readiness-public (or anything else) itself.
    for (const h of ["content-type", "accept", "last-event-id"]) if (req.headers[h]) headers[h] = req.headers[h];
    if (PUBLIC_DEMO) headers["x-readiness-public"] = "1";
    const upstream = await fetch(UPSTREAM + req.url, { method: req.method, headers, body });
    if (PUBLIC_DEMO) console.log(JSON.stringify({ proxied: req.method + " " + req.url.split("?")[0], status: upstream.status, ip: clientIp(req), public: 1 }));
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
    if (!res.headersSent) sendJson(res, 502, { error: "PROXY_ERROR", detail: String(e && e.message ? e.message : e) });
    else res.end();
  }
}

// ---- Public-demo rate limits (in memory, so readiness-web runs with --max-instances 1) ----------
// The conversational google_maps tool runs inside the model and no agent-side budget counts it,
// so this is the only bound on chat-driven Maps usage.
const LIMITED = new Set(["/run_sse", "/api/brief", "/api/verify", "/api/allocate", "/api/rehearse"]);
const PER_MIN = 10;
const PER_HOUR = 60;
const GLOBAL_PER_HOUR = 300;
const hits = new Map(); // ip -> timestamps (ms) within the last hour
let globalHits = [];

function clientIp(req) {
  const xff = String(req.headers["x-forwarded-for"] || "").split(",")[0].trim();
  return xff || req.socket.remoteAddress || "unknown";
}

function normPath(path) {
  try {
    return decodeURIComponent(path).replace(/\/{2,}/g, "/").replace(/\/$/, "") || "/";
  } catch {
    return null;
  }
}

/** Returns seconds to wait when over a limit, else 0 (and records the hit). */
function rateLimit(req, path, now = Date.now()) {
  if (!PUBLIC_DEMO || req.method !== "POST") return 0;
  const p = normPath(path);
  if (!p || !LIMITED.has(p)) return 0;
  const hour = now - 3600_000;
  const minute = now - 60_000;
  globalHits = globalHits.filter((t) => t > hour);
  const ip = clientIp(req);
  const mine = (hits.get(ip) || []).filter((t) => t > hour);
  const lastMinute = mine.filter((t) => t > minute);
  let wait = 0;
  if (lastMinute.length >= PER_MIN) wait = Math.max(wait, lastMinute[0] + 60_000 - now);
  if (mine.length >= PER_HOUR) wait = Math.max(wait, mine[0] + 3600_000 - now);
  if (globalHits.length >= GLOBAL_PER_HOUR) wait = Math.max(wait, globalHits[0] + 3600_000 - now);
  if (wait > 0) {
    hits.set(ip, mine);
    return Math.max(1, Math.ceil(wait / 1000));
  }
  mine.push(now);
  globalHits.push(now);
  hits.set(ip, mine);
  if (hits.size > 5000) for (const [k, v] of hits) if (!v.length || v[v.length - 1] <= hour) hits.delete(k);
  return 0;
}

// Checked against the decoded, slash-collapsed path, because that is what the agent routes on.
function publicDemoRefusal(req, path) {
  if (!PUBLIC_DEMO) return null;
  let p;
  try {
    p = decodeURIComponent(path).replace(/\/{2,}/g, "/");
  } catch {
    return "Malformed path.";
  }
  if (/^\/api\/plans(\/|$)/.test(p) && req.method !== "GET")
    return "Plan drafting and approval are turned off on the public demo. A signed-in county user approves plans.";
  if (/^\/apps(\/|$)/.test(p) && !PUBLIC_APPS.test(p)) return "Not available on the public demo.";
  return null;
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
  if (PROXIED.some((r) => r.test(path))) {
    const refusal = publicDemoRefusal(req, path);
    if (refusal) return sendJson(res, 403, { error: "PUBLIC_DEMO_READ_ONLY", detail: refusal });
    const wait = rateLimit(req, path);
    if (wait) {
      req.resume();
      return sendJson(res, 429, { error: "RATE_LIMITED", detail: `Demo limit reached. Try again in ${wait} s.` }, { "retry-after": String(wait) });
    }
    return void proxy(req, res);
  }
  serveStatic(req, res);
})
  .listen(PORT, () => console.log(`readiness-web on :${PORT} → ${UPSTREAM || "(no upstream)"}`))
  .on("upgrade", (req, socket, head) => void voiceUpgrade(req, socket, head));

// ---- Voice: /run_live WebSocket (Gemini Live through ADK). Live audio is costly, so it is capped. ----------
// Off on the public site unless VOICE_PUBLIC=1. 3 sessions per IP per 10 min, 2 at once, 3 minutes each.
function voiceRefuse(socket, status, text, detail) {
  try {
    socket.write(`HTTP/1.1 ${status} ${text}\r\nContent-Type: application/json\r\nConnection: close\r\n\r\n${JSON.stringify({ error: text, detail })}`);
  } finally {
    socket.destroy();
  }
}

function closeFrame(code, reason) {
  const body = Buffer.concat([Buffer.from([code >> 8, code & 255]), Buffer.from(reason).subarray(0, 120)]);
  return Buffer.concat([Buffer.from([0x88, body.length]), body]);
}

async function voiceUpgrade(req, socket, head) {
  const path = normPath((req.url || "/").split("?")[0]);
  if (path !== "/run_live") return voiceRefuse(socket, 404, "Not Found", "Unknown WebSocket path.");
  if (!VOICE_ENABLED || !UPSTREAM) return voiceRefuse(socket, 404, "Not Found", "Voice is turned off.");
  if (PUBLIC_DEMO && !VOICE_PUBLIC) return voiceRefuse(socket, 403, "Forbidden", "Voice is turned off on the public demo.");
  const ip = clientIp(req);
  const now = Date.now();
  const mine = (voiceHits.get(ip) || []).filter((t) => t > now - VOICE_WINDOW_MS);
  if (mine.length >= VOICE_PER_IP || voiceLive >= VOICE_MAX_LIVE) {
    voiceHits.set(ip, mine);
    return voiceRefuse(socket, 429, "Too Many Requests", "Voice demo limit reached. Try again later.");
  }
  mine.push(now);
  voiceHits.set(ip, mine);
  try {
    const u = new URL(UPSTREAM + req.url);
    const local = /^http:\/\/(localhost|127\.0\.0\.1)/.test(UPSTREAM);
    const headers = {
      host: u.host,
      connection: "Upgrade",
      upgrade: "websocket",
      "sec-websocket-key": req.headers["sec-websocket-key"],
      "sec-websocket-version": req.headers["sec-websocket-version"] || "13",
    };
    if (!local) headers.authorization = `Bearer ${await idToken()}`;
    if (PUBLIC_DEMO) headers["x-readiness-public"] = "1";
    const send = u.protocol === "https:" ? httpsRequest : httpRequest;
    const up = send({ hostname: u.hostname, port: u.port || (u.protocol === "https:" ? 443 : 80), path: u.pathname + u.search, method: "GET", headers });
    up.on("upgrade", (res, upSocket, upHead) => {
      let lines = "HTTP/1.1 101 Switching Protocols\r\n";
      for (const [k, v] of Object.entries(res.headers)) lines += `${k}: ${v}\r\n`;
      socket.write(lines + "\r\n");
      if (upHead && upHead.length) socket.write(upHead);
      if (head && head.length) upSocket.write(head);
      voiceLive++;
      let open = true;
      const done = () => {
        if (!open) return;
        open = false;
        voiceLive--;
        clearTimeout(timer);
        socket.destroy();
        upSocket.destroy();
      };
      const timer = setTimeout(() => {
        upSocket.unpipe(socket);
        try {
          socket.write(closeFrame(4008, "Voice session limit (3 minutes) reached."));
        } catch {
          /* gone */
        }
        setTimeout(done, 500);
      }, VOICE_MAX_MS);
      socket.pipe(upSocket);
      upSocket.pipe(socket);
      for (const s of [socket, upSocket]) {
        s.on("close", done);
        s.on("error", done);
      }
      console.log(JSON.stringify({ voice: "open", ip, live: voiceLive, public: PUBLIC_DEMO ? 1 : 0 }));
    });
    up.on("response", (res) => voiceRefuse(socket, res.statusCode || 502, res.statusMessage || "Bad Gateway", "Upstream refused the voice connection."));
    up.on("error", (e) => voiceRefuse(socket, 502, "Bad Gateway", String(e && e.message ? e.message : e)));
    up.end();
  } catch (e) {
    voiceRefuse(socket, 502, "Bad Gateway", String(e && e.message ? e.message : e));
  }
}
