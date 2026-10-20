import type { Brief } from "./types";

/** FEMA-derived content and place_id only; never Google Maps text (BUILD_SPEC.md section 11). */
export function exportBrief(b: Brief): string {
  const out = {
    ...b,
    destinations: b.destinations.map((d) => ({ shelter: d.shelter, serves: d.serves, straight_line_km: d.straight_line_km, verdict: d.verdict })),
    fixes: b.fixes.map((f) => (f.source_ids.length ? { ...f, title: null, recommendation: null } : f)),
    sources: b.sources.filter((s) => s.place_id).map((s) => ({ id: s.id, place_id: s.place_id })),
  };
  return JSON.stringify(out, null, 1);
}
