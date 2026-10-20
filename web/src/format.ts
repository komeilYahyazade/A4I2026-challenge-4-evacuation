import type { Destination, Flag, Shelter, ShelterVerification, Tract, Verdict, VerificationStatus } from "./types";

export const fmt = (v: number | null | undefined, digits = 0): string =>
  v === null || v === undefined || Number.isNaN(v)
    ? "–"
    : v.toLocaleString("en-US", { maximumFractionDigits: digits, minimumFractionDigits: 0 });
export const pct = (part: number | null | undefined, whole: number | null | undefined): number | null =>
  part == null || !whole ? null : (100 * part) / whole;
export const km = (v: number | null | undefined) => (v == null ? "–" : `${fmt(v, 1)} km`);
export const hhmm = (iso: string | null | undefined) =>
  iso ? new Date(iso).toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", timeZoneName: "short" }) : "";

export const tractLabel = (geoId: string) => {
  const base = parseInt(geoId.slice(5, 9), 10);
  const suffix = geoId.slice(9, 11);
  return `Tract ${base}${suffix !== "00" ? "." + suffix : ""}`;
};

export const FLAG_LABEL: Record<string, string> = {
  wheelchair_accessible: "Wheelchair access",
  generator_onsite: "Generator on site",
  self_sufficient_electricity: "Self-sufficient electricity",
  in_surge_slosh_area: "In storm-surge (SLOSH) zone",
  in_100_yr_floodplain: "In 100-year floodplain",
};
export const flagText = (f: Flag) => (f === "unrecorded" ? "Not recorded" : f);

export const VERDICT: Record<Verdict, { cls: string; icon: string; label: string }> = {
  holds_up: { cls: "good", icon: "✓", label: "Holds up" },
  verify_before_season: { cls: "warning", icon: "!", label: "Verify before season" },
  do_not_rely: { cls: "critical", icon: "✕", label: "Do not rely" },
};
export const VSTATUS: Record<VerificationStatus, { cls: string; icon: string; label: string }> = {
  confirmed: { cls: "good", icon: "✓", label: "Confirmed on Google Maps" },
  renamed: { cls: "warning", icon: "!", label: "Renamed" },
  closed: { cls: "critical", icon: "✕", label: "Permanently closed" },
  not_found: { cls: "critical", icon: "✕", label: "Not found" },
  unclear: { cls: "neutral", icon: "?", label: "No definite answer" },
  error: { cls: "neutral", icon: "?", label: "Check failed" },
};

export type Evidence = { cls: "yes" | "no" | "conflict" | "closed" | "unrecorded"; glyph: string; label: string; live: boolean };

/** One evidence state per shelter for pins and lists. Precedence: Maps says closed > recorded surge
 *  zone > record and Maps disagree > wheelchair answer (record first, else live) > not recorded. */
export function evidence(s: Shelter, v?: ShelterVerification | null, d?: Destination | null): Evidence {
  if (v && (v.status === "closed" || v.status === "not_found"))
    return { cls: "closed", glyph: "✕", label: v.status === "closed" ? "Closed on Google Maps" : "Not found on Google Maps", live: true };
  if (d?.verdict === "do_not_rely") return { cls: "closed", glyph: "✕", label: "Do not rely (brief)", live: !!v };
  if (s.in_surge_slosh_area === "yes") return { cls: "closed", glyph: "≋", label: "In surge zone (record)", live: false };
  const maps = v?.wheelchair.maps;
  if (v?.wheelchair.agreement === "disagree")
    return { cls: "conflict", glyph: "!", label: "Record and Google Maps disagree on wheelchair access", live: true };
  if (s.wheelchair_accessible === "yes") return { cls: "yes", glyph: "✓", label: "Wheelchair: recorded yes", live: maps === "yes" };
  if (s.wheelchair_accessible === "no") return { cls: "no", glyph: "–", label: "Wheelchair: recorded no", live: maps === "no" };
  if (maps === "yes") return { cls: "yes", glyph: "✓", label: "Wheelchair: Google Maps says yes; FEMA record blank", live: true };
  if (maps === "no") return { cls: "no", glyph: "–", label: "Wheelchair: Google Maps says no; FEMA record blank", live: true };
  return { cls: "unrecorded", glyph: "?", label: "Wheelchair access not recorded", live: false };
}

/** Walkable vs pickup needed, by straight-line km to the nearest shelter of any kind. */
export function reachClass(t: Tract, reachKm: number): "walkable" | "pickup" | "unknown" {
  if (t.nearest_shelter_km == null) return "unknown";
  return t.nearest_shelter_km <= reachKm ? "walkable" : "pickup";
}

/** Sequential blue ramp (light -> dark) for need score, by percentile within the county. */
export const RAMP = ["#b7d3f6", "#9ec5f4", "#86b6ef", "#6da7ec", "#5598e7", "#3987e5", "#2a78d6", "#256abf", "#1c5cab", "#184f95", "#104281"];
export function rampColor(pctRank: number) {
  return RAMP[Math.min(RAMP.length - 1, Math.max(0, Math.floor(pctRank * RAMP.length)))];
}
export function percentileRanks(values: (number | null)[]): number[] {
  const sorted = values.filter((v): v is number => v != null).sort((a, b) => a - b);
  return values.map((v) => (v == null || !sorted.length ? 0 : sorted.findIndex((x) => x >= v) / sorted.length));
}

/** Straight-line km between two points (haversine). */
export function haversineKm(a: { lat: number; lon: number }, b: { lat: number; lon: number }): number {
  const R = 6371, rad = Math.PI / 180;
  const dLat = (b.lat - a.lat) * rad, dLon = (b.lon - a.lon) * rad;
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(a.lat * rad) * Math.cos(b.lat * rad) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(h));
}
