// Pure helpers that turn wire rows into what the UI draws. No network, no state.
import type {
  CountyMap,
  LatLng,
  LiveCheck,
  MapsSource,
  ReadinessBrief,
  ShelterRecord,
  ThreeState,
  WhereRow,
} from "./contract";

export type Existence = "VERIFIED_LIVE" | "RENAMED" | "CONFLICT" | "DROPPED" | "FEMA_ONLY";
export type AccessEvidence =
  | "RECORDED_YES"
  | "RECORDED_NO"
  | "LIVE_YES"
  | "LIVE_NO"
  | "CONFLICT"
  | "NOBODY_KNOWS";
export type BasisCode = "ROUTED" | "MODEL_REPORTED" | "STRAIGHT_LINE";

/** Accepts lat/lng, lat/lon, latitude/longitude. Returns null when any part is missing. */
export function coords(o: unknown): LatLng | null {
  if (!o || typeof o !== "object") return null;
  const r = o as Record<string, unknown>;
  const lat = num(r.lat ?? r.latitude);
  const lng = num(r.lng ?? r.lon ?? r.longitude);
  return lat === null || lng === null ? null : { lat, lng };
}

function num(v: unknown): number | null {
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (typeof v === "string" && v.trim() !== "" && Number.isFinite(Number(v))) return Number(v);
  return null;
}

export function shelterName(row: { name?: string; shelter_name?: string; shelter_id: string }): string {
  return row.name || row.shelter_name || `Shelter ${row.shelter_id}`;
}

export function capacityOf(rec?: ShelterRecord): number | null {
  if (!rec) return null;
  return rec.capacity ?? rec.evacuation_capacity ?? null;
}

export function threeState(v: unknown): ThreeState {
  if (v === true) return "YES";
  if (v === false) return "NO";
  const s = typeof v === "string" ? v.trim().toUpperCase() : "";
  if (s === "YES" || s === "TRUE" || s === "Y") return "YES";
  if (s === "NO" || s === "FALSE" || s === "N") return "NO";
  return "UNRECORDED";
}

function norm(s?: string | null): string {
  return (s || "")
    .toLowerCase()
    .replace(/\bms\b/g, "middle school")
    .replace(/\bhs\b/g, "high school")
    .replace(/\bes\b/g, "elementary school")
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
}

/** Existence axis: is the building there and is it what the record says? */
export function existenceOf(row: Pick<WhereRow, "live" | "verdict" | "flags" | "name" | "shelter_name">): Existence {
  const live = row.live;
  if (row.verdict === "DROP") return "DROPPED";
  if (!live || live.status !== "VERIFIED" || !(live.sources || []).length) return "FEMA_ONLY";
  const flags = row.flags || [];
  if (flags.includes("TEMPORARILY_CLOSED") || live.closed === "temporarily") return "CONFLICT";
  const recName = row.name || row.shelter_name;
  if (
    flags.includes("NAME_DIFFERS") ||
    (live.current_name && recName && norm(live.current_name) !== norm(recName))
  )
    return "RENAMED";
  if (live.exists === true) return "VERIFIED_LIVE";
  return "FEMA_ONLY";
}

/** Access axis: what we know about wheelchair entry, and from whom. Unknown never becomes NO. */
export function accessOf(record: ThreeState | undefined, live: LiveCheck | null | undefined): AccessEvidence {
  const rec = record || "UNRECORDED";
  const usable = live && live.status === "VERIFIED" && (live.sources || []).length > 0;
  const ent = usable ? live!.wheelchair_entrance : "unknown";
  if (ent === "yes" || ent === "no") {
    if (rec !== "UNRECORDED" && (ent === "yes") !== (rec === "YES")) return "CONFLICT";
    return ent === "yes" ? "LIVE_YES" : "LIVE_NO";
  }
  if (rec === "YES") return "RECORDED_YES";
  if (rec === "NO") return "RECORDED_NO";
  return "NOBODY_KNOWS";
}

export function basisCode(s?: string | null): BasisCode {
  const t = (s || "").toUpperCase();
  if (t.startsWith("ROUTED")) return "ROUTED";
  if (t.startsWith("MODEL")) return "MODEL_REPORTED";
  return "STRAIGHT_LINE";
}

export const BASIS_TEXT: Record<BasisCode, string> = {
  ROUTED: "Routed by Google Maps (typical traffic, not evacuation traffic)",
  MODEL_REPORTED: "Model-reported drive time (no route source returned)",
  STRAIGHT_LINE: "Straight-line distance (a lower bound on road distance)",
};

/** Merge verification rows into the brief by shelter_id; recompute deduplicated maps_sources. */
export function mergeVerification(brief: ReadinessBrief, results: WhereRow[]): ReadinessBrief {
  const byId = new Map(results.map((r) => [String(r.shelter_id), r]));
  const rows = brief.where_they_would_go.map((row) => {
    const v = byId.get(String(row.shelter_id));
    if (!v) return row;
    const inactive = !!(row.record?.name_says_inactive || v.record?.name_says_inactive || (v.flags || []).includes("NAME_SAYS_INACTIVE"));
    return {
      ...row,
      record: row.record ? { ...row.record, name_says_inactive: inactive } : v.record,
      live: v.live,
      accessibility: v.accessibility,
      verdict: v.verdict,
      why: v.why,
      flags: v.flags ?? row.flags,
      lat: row.lat ?? v.lat,
      lng: row.lng ?? v.lng,
    };
  });
  // Shelters checked from the fix list (not a destination of a top tract) are kept, not dropped.
  const known = new Set(brief.where_they_would_go.map((r) => String(r.shelter_id)));
  for (const v of results) {
    if (known.has(String(v.shelter_id))) continue;
    rows.push({ ...v, name: v.name || v.shelter_name, for_tract: undefined });
  }
  return { ...brief, where_they_would_go: rows, maps_sources: collectSources(rows) };
}

export function collectSources(rows: WhereRow[]): MapsSource[] {
  const out = new Map<string, MapsSource>();
  for (const r of rows) {
    for (const s of r.live?.sources || []) {
      const key = s.place_id || s.uri || s.title || "";
      const e = out.get(key) || { ...s, supports_shelter_ids: [] };
      if (!e.supports_shelter_ids!.includes(r.shelter_id)) e.supports_shelter_ids!.push(r.shelter_id);
      out.set(key, e);
    }
  }
  return [...out.values()];
}

/** Coordinates lookup across every payload we hold, so rows without lat/lng still draw. */
export function buildIndex(map: CountyMap | null) {
  const tracts = new Map<string, LatLng>();
  const shelters = new Map<string, LatLng>();
  for (const t of map?.tracts || []) {
    const c = coords(t);
    if (c) tracts.set(t.geo_id, c);
  }
  for (const s of map?.shelters || []) {
    const c = coords(s);
    if (c) shelters.set(String(s.shelter_id), c);
  }
  return { tracts, shelters };
}

export function ago(iso?: string | null, now = Date.now()): string {
  if (!iso) return "";
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return "";
  const s = Math.max(0, Math.round((now - t) / 1000));
  if (s < 90) return `${s} s ago`;
  if (s < 5400) return `${Math.round(s / 60)} min ago`;
  if (s < 172800) return `${Math.round(s / 3600)} h ago`;
  return new Date(t).toLocaleDateString();
}

export function fmt(n: number | null | undefined, digits = 0): string {
  if (n === null || n === undefined || !Number.isFinite(n)) return "—";
  return n.toLocaleString(undefined, { maximumFractionDigits: digits, minimumFractionDigits: 0 });
}

export function pct(n: number | null | undefined, digits = 0): string {
  if (n === null || n === undefined || !Number.isFinite(n)) return "—";
  return `${(n * 100).toFixed(digits)}%`;
}

/** Ray-casting point-in-polygon on GeoJSON [lng, lat] rings (outer ring minus holes). */
export function inGeometry(p: LatLng, g: { type: string; coordinates: unknown } | null | undefined): boolean {
  if (!g) return false;
  const polys = (g.type === "Polygon" ? [g.coordinates] : g.type === "MultiPolygon" ? g.coordinates : []) as number[][][][];
  const inRing = (ring: number[][]) => {
    let inside = false;
    for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
      const [xi, yi] = ring[i];
      const [xj, yj] = ring[j];
      if (yi > p.lat !== yj > p.lat && p.lng < ((xj - xi) * (p.lat - yi)) / (yj - yi || 1e-12) + xi) inside = !inside;
    }
    return inside;
  };
  return polys.some((rings) => rings.length > 0 && inRing(rings[0]) && !rings.slice(1).some(inRing));
}

/** How a shelters_down reason should be labelled (data lane guidance). */
export function downReasonLabel(reason: string): { label: "RECORDED" | "APPROXIMATE"; source: string } {
  return reason === "IN_SURGE_ZONE" ? { label: "RECORDED", source: "FEMA" } : { label: "APPROXIMATE", source: "NRI" };
}
