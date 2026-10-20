import type { AccessEvidence, Existence } from "./derive";

// Fill = what we know about wheelchair entry. Ring = whether the building is confirmed live.
// UNRECORDED (NOBODY_KNOWS) has its own grey and a "?" glyph. It is never drawn like NO.
export const ACCESS: Record<AccessEvidence, { color: string; glyph: string; text: string }> = {
  RECORDED_YES: { color: "#2e7d32", glyph: "✓", text: "Wheelchair YES (FEMA record)" },
  LIVE_YES: { color: "#00897b", glyph: "✓", text: "Wheelchair entrance YES (Google Maps, live)" },
  RECORDED_NO: { color: "#e65100", glyph: "✕", text: "Wheelchair NO (FEMA record)" },
  LIVE_NO: { color: "#bf360c", glyph: "✕", text: "Wheelchair entrance NO (Google Maps, live)" },
  CONFLICT: { color: "#f9a825", glyph: "!", text: "FEMA and Google Maps disagree: send someone" },
  NOBODY_KNOWS: { color: "#9e9e9e", glyph: "?", text: "Nobody has recorded it (UNRECORDED, not NO)" },
};

export const EXISTENCE: Record<Existence, { text: string }> = {
  VERIFIED_LIVE: { text: "Building confirmed on Google Maps (live)" },
  RENAMED: { text: "Confirmed, but known under another name" },
  CONFLICT: { text: "Live check raised a problem (e.g. temporarily closed)" },
  DROPPED: { text: "Not found or permanently closed: dropped" },
  FEMA_ONLY: { text: "FEMA record only: not checked live" },
};

export const MODE_COLOR: Record<string, string> = {
  WALK: "#2e7d32",
  PARATRANSIT: "#1565c0",
  LONG_RUN: "#ef6c00",
};

export const GAP_RED = "#d32f2f";
export const TRACT_TOP = "#6a1b9a";
export const TRACT_BASE = "#5c6bc0";
export const TRACT_MUST = "#00838f";
