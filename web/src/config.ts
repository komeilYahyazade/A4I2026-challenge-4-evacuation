// Runtime config from /config.js (server.py, from env vars): never built into the bundle.
export interface RuntimeConfig { MAPS_API_KEY?: string; MAP_ID?: string; VOICE_ENABLED?: boolean }
export const CONFIG: RuntimeConfig = (window as unknown as { __CONFIG__?: RuntimeConfig }).__CONFIG__ ?? {};
export const USER_ID = "web";
