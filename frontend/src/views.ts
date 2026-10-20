// Merge every payload we hold into one drawable list of shelters and one of tracts.
// Base layer: county_map.v1. Enriched by allocation.v1, the brief and verification rows.
import type { Assignment, FixItem, LatLng, SiteHazard, ThreeState, TractEntry, WhereRow } from "./contract";
import { accessOf, coords, existenceOf, shelterName, type AccessEvidence, type Existence } from "./derive";
import type { State } from "./store";

export interface ShelterView {
  id: string;
  name: string;
  address?: string | null;
  at: LatLng;
  usage?: string;
  isPre: boolean;
  capacity: number | null;
  capacityStatus: "RECORDED" | "UNRECORDED";
  implausible: boolean;
  wheelchair: ThreeState;
  generator: ThreeState;
  surge: ThreeState;
  isMedical: boolean;
  siteHazard?: SiteHazard | null;
  nameInactive?: boolean;
  row?: WhereRow;
  existence: Existence;
  access: AccessEvidence;
  assigned?: number;
  utilization?: number;
  excluded?: string | null;
  down?: string;
}

export interface TractView {
  geoId: string;
  at: LatLng;
  householdsNoVehicle: number;
  persons?: number;
  share?: number;
  entry?: TractEntry;
  mustLeave?: TractEntry;
  unassigned?: number;
  demand?: number;
}

export function shelterViews(s: State): ShelterView[] {
  const out = new Map<string, ShelterView>();
  const put = (id: string, patch: Omit<Partial<ShelterView>, "at"> & { at?: LatLng | null }) => {
    const prev = out.get(id);
    if (!prev && !patch.at) return;
    const base: ShelterView =
      prev ||
      ({
        id,
        name: `Shelter ${id}`,
        at: patch.at!,
        isPre: true,
        capacity: null,
        capacityStatus: "UNRECORDED",
        implausible: false,
        wheelchair: "UNRECORDED",
        generator: "UNRECORDED",
        surge: "UNRECORDED",
        isMedical: false,
        existence: "FEMA_ONLY",
        access: "NOBODY_KNOWS",
      } as ShelterView);
    const next = { ...base, ...Object.fromEntries(Object.entries(patch).filter(([, v]) => v !== undefined && v !== null)) } as ShelterView;
    out.set(id, next);
  };

  for (const m of s.map?.shelters || []) {
    put(String(m.shelter_id), {
      name: m.name,
      address: m.address,
      at: coords(m),
      usage: m.usage,
      isPre: m.is_pre_landfall !== false && m.usage !== "POST",
      capacity: m.capacity ?? null,
      capacityStatus: m.capacity_status || (m.capacity == null ? "UNRECORDED" : "RECORDED"),
      implausible: !!m.capacity_implausible,
      wheelchair: m.wheelchair,
      generator: m.generator,
      surge: m.surge,
      isMedical: !!m.is_medical,
    });
  }
  const alloc = s.rehearsalMode && s.rehearsal ? s.rehearsal.after : s.allocation;
  for (const a of alloc?.shelters || []) {
    put(String(a.shelter_id), {
      name: a.name,
      at: coords(a),
      capacity: a.capacity ?? undefined,
      wheelchair: a.wheelchair,
      generator: a.generator,
      surge: a.surge,
      assigned: a.assigned_persons,
      utilization: a.utilization,
      excluded: a.excluded_reason ?? null,
    });
  }
  for (const r of s.brief?.where_they_would_go || []) {
    const rec = r.record || {};
    put(String(r.shelter_id), {
      name: shelterName(r),
      address: r.address,
      at: coords(r),
      usage: rec.usage,
      capacity: rec.capacity ?? rec.evacuation_capacity ?? undefined,
      capacityStatus: rec.capacity_status,
      implausible: rec.capacity_implausible,
      wheelchair: rec.wheelchair,
      generator: rec.generator,
      surge: rec.surge,
      isMedical: rec.is_medical,
      siteHazard: rec.site_hazard,
      nameInactive: rec.name_says_inactive || (r.flags || []).includes("NAME_SAYS_INACTIVE") || /\b(INACTIVE|CLOSED|DEMOLISHED|DO NOT USE)\b/i.test(shelterName(r)),
      row: r,
    });
  }
  if (s.rehearsalMode && s.rehearsal) {
    for (const d of s.rehearsal.shelters_down) put(String(d.shelter_id), { name: d.name, at: coords(d), down: d.reason });
  }
  for (const v of out.values()) {
    v.existence = v.row ? existenceOf(v.row) : "FEMA_ONLY";
    v.access = accessOf(v.wheelchair, v.row?.live);
  }
  return [...out.values()];
}

export function tractViews(s: State): TractView[] {
  const out = new Map<string, TractView>();
  for (const t of s.map?.tracts || []) {
    const at = coords(t);
    if (!at) continue;
    out.set(t.geo_id, {
      geoId: t.geo_id,
      at,
      householdsNoVehicle: t.households_no_vehicle || 0,
      persons: t.est_persons_no_vehicle,
      share: t.share_no_vehicle,
    });
  }
  const alloc = s.rehearsalMode && s.rehearsal ? s.rehearsal.after : s.allocation;
  for (const t of alloc?.tracts || []) {
    const at = coords(t);
    const prev = out.get(t.geo_id);
    if (!prev && !at) continue;
    out.set(t.geo_id, {
      ...(prev || { geoId: t.geo_id, at: at!, householdsNoVehicle: t.households_no_vehicle || 0 }),
      demand: t.demand_persons,
      unassigned: t.unassigned_persons,
    });
  }
  for (const e of s.brief?.who_cannot_leave || []) {
    const at = coords(e) || out.get(e.geo_id)?.at;
    if (!at) continue;
    const prev = out.get(e.geo_id);
    out.set(e.geo_id, {
      ...(prev || { geoId: e.geo_id, at, householdsNoVehicle: e.people?.households_no_vehicle || 0 }),
      entry: e,
    });
  }
  for (const e of s.brief?.must_leave_may_need_help || []) {
    const at = coords(e) || out.get(e.geo_id)?.at;
    if (!at) continue;
    const prev = out.get(e.geo_id);
    out.set(e.geo_id, {
      ...(prev || { geoId: e.geo_id, at, householdsNoVehicle: e.people?.households_no_vehicle || 0 }),
      mustLeave: e,
    });
  }
  return [...out.values()];
}

export interface Line {
  key: string;
  from: LatLng;
  to: LatLng;
  persons: number;
  mode: string;
  routed: boolean;
  kind: "plan" | "reassigned" | "old";
}

export function lines(s: State, tracts: TractView[], shelters: ShelterView[]): Line[] {
  const tAt = new Map(tracts.map((t) => [t.geoId, t.at]));
  const sAt = new Map(shelters.map((x) => [x.id, x.at]));
  const toLine = (a: Assignment, kind: Line["kind"]): Line | null => {
    const from = a.from || tAt.get(a.geo_id);
    const to = a.to || sAt.get(String(a.shelter_id));
    if (!from || !to) return null;
    return {
      key: `${kind}:${a.geo_id}:${a.shelter_id}:${a.persons}`,
      from,
      to,
      persons: a.persons,
      mode: a.mode,
      routed: (a.distance_basis || "").toUpperCase().startsWith("ROUTED"),
      kind,
    };
  };
  if (s.rehearsalMode && s.rehearsal) {
    const r = s.rehearsal;
    const moved = new Set(r.diff.reassigned.map((x) => `${x.geo_id}:${x.to_shelter_id}`));
    const out: Line[] = [];
    for (const a of r.after.assignments) {
      const l = toLine(a, moved.has(`${a.geo_id}:${a.shelter_id}`) ? "reassigned" : "plan");
      if (l) out.push(l);
    }
    for (const x of r.diff.reassigned) {
      const from = x.from || tAt.get(x.geo_id);
      const to = x.to_old || sAt.get(x.from_shelter_id);
      if (from && to) out.push({ key: `old:${x.geo_id}:${x.from_shelter_id}:${out.length}`, from, to, persons: x.persons || 0, mode: "OLD", routed: false, kind: "old" });
    }
    return out;
  }
  return (s.allocation?.assignments || []).map((a) => toLine(a, "plan")).filter((x): x is Line => !!x);
}

export interface GapMark {
  key: string;
  at: LatLng;
  kind: string;
  severity: string;
  item: FixItem;
  idx: number;
  source: "brief" | "plan";
}

export function gapMarks(s: State, tracts: TractView[], shelters: ShelterView[]): GapMark[] {
  const tAt = new Map(tracts.map((t) => [t.geoId, t.at]));
  const sAt = new Map(shelters.map((x) => [x.id, x.at]));
  const out: GapMark[] = [];
  const add = (items: FixItem[] | undefined, source: GapMark["source"], offset: number) =>
    (items || []).forEach((f, i) => {
      const ref = f.ref;
      if (!ref || ref.kind === "county") return;
      const at = coords(ref) || (ref.kind === "tract" ? tAt.get(ref.id) : sAt.get(ref.id));
      if (!at) return;
      out.push({ key: `${source}:${i}`, at, kind: ref.kind, severity: f.severity || "MEDIUM", item: f, idx: offset + i, source });
    });
  add(s.brief?.fix_before_season, "brief", 0);
  const alloc = s.rehearsalMode && s.rehearsal ? s.rehearsal.after : s.allocation;
  add(alloc?.gaps, "plan", 1000);
  return out;
}
