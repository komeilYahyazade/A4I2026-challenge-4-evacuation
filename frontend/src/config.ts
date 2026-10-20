// Runtime config: /config.js (window.__READINESS_CONFIG__) wins, then VITE_* build env.
// AGENT_URL is empty in both dev and production: the UI calls same-origin /api, /apps and /run_sse,
// and a proxy (Vite in dev, server.mjs on Cloud Run) adds the ID token for the private agent.
// MODE=mock serves the synthetic contract fixtures, with a MOCK DATA badge.

interface RuntimeConfig {
  MODE?: string;
  AGENT_URL?: string;
  APP?: string;
  MAPS_API_KEY?: string;
  MAP_ID?: string;
  USER_ID?: string;
}

declare global {
  interface Window {
    __READINESS_CONFIG__?: RuntimeConfig;
  }
}

const rt: RuntimeConfig = (typeof window !== "undefined" && window.__READINESS_CONFIG__) || {};
const env = import.meta.env;

function pick(a: string | undefined, b: string | undefined, fallback = ""): string {
  if (a !== undefined && a !== "") return a;
  if (b !== undefined && b !== "") return b;
  return fallback;
}

export const CONFIG = {
  agentUrl: pick(rt.AGENT_URL, env.VITE_AGENT_URL as string | undefined).replace(/\/$/, ""),
  app: pick(rt.APP, env.VITE_APP as string | undefined, "agent"),
  mapsApiKey: pick(rt.MAPS_API_KEY, env.VITE_MAPS_API_KEY as string | undefined),
  mapId: pick(rt.MAP_ID, env.VITE_MAP_ID as string | undefined, "DEMO_MAP_ID"),
  userId: pick(rt.USER_ID, undefined, "em-lee"),
  countyFips: "12071",
};

export const MOCK_ONLY = pick(rt.MODE, env.VITE_MODE as string | undefined).toLowerCase() === "mock";
