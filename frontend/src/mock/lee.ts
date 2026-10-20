// MOCK mode: the agent lane's contract fixtures (contract/fixtures, synced into src/fixtures by
// `npm run sync-fixtures`). Their shapes are exactly what the agent emits; their contents are
// synthetic, and the UI shows a MOCK DATA badge whenever any of them is on screen.
import allocation from "../fixtures/allocation.sample.json";
import countyMap from "../fixtures/county_map.lee.json";
import brief from "../fixtures/readiness_brief.lee.json";
import rehearsal from "../fixtures/rehearsal.ian.sample.json";
import verification from "../fixtures/shelter_verification.sample.json";
import storm from "../fixtures/storm_track.ian.sample.json";
import type {
  Allocation,
  CountyMap,
  ReadinessBrief,
  Rehearsal,
  ShelterVerification,
  StormTrack,
  WhereRow,
} from "../contract";

const clone = <T,>(x: unknown): T => JSON.parse(JSON.stringify(x)) as T;

export const mockMap = clone<CountyMap>(countyMap);
export const mockAllocation = clone<Allocation>(allocation);
export const mockBrief = clone<ReadinessBrief>(brief);
export const mockStorm = clone<StormTrack>(storm);
export const mockRehearsal = () => clone<Rehearsal>(rehearsal);

/** The fixture's two verification rows, re-keyed onto whichever shelters the UI asked about. */
export function mockVerify(ids: string[], rows: WhereRow[] = []): ShelterVerification {
  const v = clone<ShelterVerification>(verification);
  const now = new Date().toISOString();
  v.verified_at = now;
  v.results = ids.map((id, i) => {
    const tpl = v.results[i % v.results.length];
    const row = rows.find((r) => r.shelter_id === id);
    const renamed = (tpl.flags || []).includes("NAME_DIFFERS");
    const name = row?.name ?? tpl.name;
    const rec = row?.record?.wheelchair || "UNRECORDED";
    const ent = tpl.live?.wheelchair_entrance || "unknown";
    // Same rules as agent/verify.py, so the mock never shows an impossible combination.
    const conflict = (ent === "yes" || ent === "no") && rec !== "UNRECORDED" && (ent === "yes") !== (rec === "YES");
    const accessibility =
      ent === "yes" || ent === "no"
        ? {
            label: "VERIFIED LIVE" as const,
            summary: conflict
              ? `FEMA records wheelchair access as ${rec}; Google Maps reports the entrance as ${ent}.`
              : rec === "UNRECORDED"
                ? `No FEMA record. Google Maps reports a wheelchair-accessible entrance: ${ent}. That is evidence about an entrance, not an ADA certification.`
                : `FEMA records wheelchair access as ${rec}; Google Maps agrees (${ent}).`,
          }
        : rec === "UNRECORDED"
          ? { label: "UNRECORDED" as const, summary: "Nobody has recorded whether a wheelchair user can get in, and Google Maps did not say." }
          : { label: "RECORDED" as const, summary: `FEMA records wheelchair access as ${rec}. Google Maps did not say.` };
    return {
      ...tpl,
      shelter_id: id,
      name,
      shelter_name: name,
      lat: row?.lat ?? tpl.lat,
      lng: row?.lng ?? tpl.lng,
      record: row?.record ?? tpl.record,
      accessibility,
      flags: [...(renamed ? ["NAME_DIFFERS"] : []), ...(conflict ? ["ACCESSIBILITY_CONFLICT"] : [])],
      live: tpl.live
        ? { ...tpl.live, verified_at: now, current_name: renamed ? tpl.live.current_name : name, sources: (tpl.live.sources || []).map((x) => ({ ...x, title: renamed ? x.title : `${name} (mock source)` })) }
        : null,
    };
  });
  return v;
}

export const FIXTURE_NOTE =
  (brief as { _note?: string })._note ||
  "Synthetic contract fixtures. Not real shelters, tracts or Google Maps results.";
