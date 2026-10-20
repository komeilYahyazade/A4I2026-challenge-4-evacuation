// Design tokens, mirrored in styles.css :root. One place for colour; meaning is never colour alone
// (every state also has its own shape and glyph).
import type { AccessEvidence, Existence } from "./derive";

export const T = {
  bg: "#0d1117",
  surface: "#161b22",
  raised: "#1f2630",
  border: "#30363d",
  text: "#e6edf3",
  muted: "#8b949e",
  accent: "#4493f8",
  danger: "#f85149",
  warn: "#d29922",
  ok: "#3fb950",
  info: "#58a6ff",
  unrec: "#8b949e",
  tract: "#a371f7",
  must: "#39c5cf",
  site: "#e3b341",
};

export const ACCESS: Record<AccessEvidence, { color: string; glyph: string; text: string }> = {
  RECORDED_YES: { color: T.ok, glyph: "✓", text: "Wheelchair access recorded YES (FEMA)" },
  LIVE_YES: { color: T.ok, glyph: "✓", text: "Wheelchair entrance YES on Google Maps" },
  RECORDED_NO: { color: T.warn, glyph: "✕", text: "Wheelchair access recorded NO (FEMA)" },
  LIVE_NO: { color: T.warn, glyph: "✕", text: "Wheelchair entrance NO on Google Maps" },
  CONFLICT: { color: T.warn, glyph: "!", text: "FEMA and Google Maps disagree" },
  NOBODY_KNOWS: { color: T.unrec, glyph: "?", text: "Wheelchair access not recorded (not the same as NO)" },
};

export const EXISTENCE: Record<Existence, { text: string }> = {
  VERIFIED_LIVE: { text: "Confirmed on Google Maps" },
  RENAMED: { text: "Confirmed, now under another name" },
  CONFLICT: { text: "Checked, but still unconfirmed" },
  DROPPED: { text: "Dropped: not found, closed, or another organisation now" },
  FEMA_ONLY: { text: "FEMA record only, not checked live" },
};

export const MODE_COLOR: Record<string, string> = { WALK: T.ok, PARATRANSIT: T.accent, LONG_RUN: T.warn };
export const GAP_RED = T.danger;
export const TRACT_TOP = T.tract;
export const TRACT_BASE = "#6e5aa8";
export const TRACT_MUST = T.must;
