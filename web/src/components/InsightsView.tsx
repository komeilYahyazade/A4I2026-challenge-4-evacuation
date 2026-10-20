import { useMemo, useState } from "react";
import type { Brief, County, CountyInsights, CountyMap, Tract } from "../types";
import { FLAG_LABEL, fmt, km, pct, reachClass, tractLabel } from "../format";
import { GoogleMaps, Tag } from "./Attribution";
import { AccessScatter, PairBars, RankBars, Tile, TriBar } from "./Charts";

type SortKey = "need_score" | "households_no_vehicle" | "pct_age_65_plus" | "pct_disability" | "mobile_homes" | "nearest_shelter_km" | "nearest_wheelchair_yes_km" | "exposure_score";
const COLS: { key: SortKey; label: string; digits: number; kind: "recorded" | "modeled" | "derived" }[] = [
  { key: "need_score", label: "Need", digits: 3, kind: "derived" },
  { key: "households_no_vehicle", label: "No-car hh", digits: 0, kind: "recorded" },
  { key: "pct_age_65_plus", label: "65+ %", digits: 1, kind: "recorded" },
  { key: "pct_disability", label: "Disability %", digits: 1, kind: "recorded" },
  { key: "mobile_homes", label: "Mobile homes", digits: 0, kind: "recorded" },
  { key: "exposure_score", label: "Flood/hurricane exposure", digits: 2, kind: "recorded" },
  { key: "nearest_shelter_km", label: "Any shelter km", digits: 1, kind: "derived" },
  { key: "nearest_wheelchair_yes_km", label: "Accessible km", digits: 1, kind: "derived" },
];

export function InsightsView({ ins, map, counties, county, brief, reachKm, onSelectTract, onCounty }: {
  ins: CountyInsights | null; map: CountyMap | null; counties: County[]; county: string; brief: Brief | null; reachKm: number;
  onSelectTract: (geoId: string) => void; onCounty: (fips: string) => void;
}) {
  const [scope, setScope] = useState<"evac" | "all">("evac");
  const [sort, setSort] = useState<{ key: SortKey; desc: boolean }>({ key: "need_score", desc: true });
  const [pickupOnly, setPickupOnly] = useState(false);
  const [q, setQ] = useState("");

  const tracts = map?.tracts ?? [];
  const farMany = useMemo(() => tracts.filter((t) => (t.nearest_wheelchair_yes_km ?? 99) > reachKm)
    .sort((a, b) => (b.households_no_vehicle ?? 0) - (a.households_no_vehicle ?? 0)).slice(0, 8), [tracts, reachKm]);
  const rows = useMemo(() => {
    const r = tracts.filter((t) => (!pickupOnly || reachClass(t, reachKm) === "pickup") && (!q || t.geo_id.includes(q) || tractLabel(t.geo_id).toLowerCase().includes(q.toLowerCase())));
    const v = (t: Tract) => (t[sort.key] as number | null | undefined) ?? (sort.desc ? -Infinity : Infinity);
    return [...r].sort((a, b) => (sort.desc ? v(b) - v(a) : v(a) - v(b)));
  }, [tracts, sort, pickupOnly, q, reachKm]);
  const pickupCount = useMemo(() => tracts.filter((t) => reachClass(t, reachKm) === "pickup").length, [tracts, reachKm]);
  const ranked = useMemo(() => [...counties].sort((a, b) => a.rank - b.rank), [counties]);
  const selCounty = counties.find((c) => c.county_fips === county);

  if (!ins || !map) return <div className="dash"><div className="card span-12 empty">Loading insights…</div></div>;
  const c = ins.access.county, s = ins.access.state, bc = ins.baseline.county, bs = ins.baseline.state;
  const reachAny = pct(c.households_no_vehicle_within_reach_any, c.households_no_vehicle);
  const reachWc = pct(c.households_no_vehicle_within_reach_wheelchair_yes, c.households_no_vehicle);
  const flagRows = ins.flags.filter((r) => r.scope === scope);
  const wcEvac = ins.flags.find((r) => r.scope === "evac" && r.flag === "wheelchair_accessible");
  const maxRank = Math.max(...ranked.map((x) => (x.need_score ?? 0) * (x.exposure_score ?? 0)), 1e-9);

  return (
    <div className="dash">
      <div className="card span-12">
        <h2>{ins.name}: who cannot leave, and where they could go</h2>
        <div className="sub">All figures are historical federal records (FEMA, ACS, CDC, CMS, HHS) computed in BigQuery. Distances are straight-line km from tract centroids; "reachable" = within {ins.reachable_km} km.</div>
        <div className="tiles">
          <Tile value={fmt(c.households_no_vehicle)} label="households without a car" ctx={`${fmt(bc.pct_no_vehicle, 1)}% of households · state ${fmt(bs.pct_no_vehicle, 1)}%`} />
          <Tile value={`${fmt(reachAny, 0)}%`} label={`of them within ${ins.reachable_km} km of any shelter`} ctx={`state ${fmt(pct(s.households_no_vehicle_within_reach_any, s.households_no_vehicle), 0)}%`} />
          <Tile value={<span style={{ color: "var(--critical)" }}>{fmt(reachWc, 0)}%</span>} label={`within ${ins.reachable_km} km of a shelter recorded wheelchair accessible`} ctx={`state ${fmt(pct(s.households_no_vehicle_within_reach_wheelchair_yes, s.households_no_vehicle), 0)}%`} />
          <Tile value={km(c.median_km_wheelchair_yes)} label="median distance to a recorded-accessible shelter" ctx={`any shelter ${km(c.median_km_any)} · state ${km(s.median_km_wheelchair_yes)}`} />
          <Tile value={fmt(c.people_over_10km_any)} label="people more than 10 km from any shelter" ctx={`state ${fmt(s.people_over_10km_any)}`} />
          <Tile value={fmt(ins.power.power_dependent_dme)} label="power-dependent Medicare beneficiaries" tag={<Tag kind="county" />} ctx={`${fmt(ins.power.medical_capacity)} medical-needs spaces recorded`} />
          <Tile value={fmt(ins.care.certified_beds_far_from_generator)} label={`nursing-home beds with no recorded-generator shelter within ${ins.care.threshold_km} km`} ctx={`${ins.care.far_from_generator} of ${ins.care.facilities} facilities`} />
        </div>
        <div className="insight">
          If you need an accessible shelter, the nearest one <b>on record</b> is {km(c.median_km_wheelchair_yes)} away (median), against {km(c.median_km_any)} for any shelter.
          That is a fact about the paperwork as much as the buildings: {wcEvac ? `${fmt(wcEvac.unrecorded)} of ${fmt(wcEvac.yes + wcEvac.no + wcEvac.unrecorded)}` : "most"} pre-landfall shelters have no wheelchair answer at all.
        </div>
      </div>

      <div className="card span-6">
        <h2>The paperwork gap</h2>
        <div className="sub">Recorded flags in three states. Unrecorded is not no; counts measure what was filled in, so never rank counties on them.</div>
        <div className="filters">
          <button aria-pressed={scope === "evac"} onClick={() => setScope("evac")}>Pre-landfall (EVAC) · {ins.shelters.evac}</button>
          <button aria-pressed={scope === "all"} onClick={() => setScope("all")}>All shelters · {ins.shelters.total}</button>
        </div>
        {flagRows.map((r) => <TriBar key={r.flag} label={FLAG_LABEL[r.flag]} yes={r.yes} no={r.no} unrecorded={r.unrecorded}
          note={r.flag === "in_surge_slosh_area" ? "blank ≠ outside the zone" : undefined} />)}
        <div className="small muted">Capacity: {fmt(ins.shelters.capacity_recorded)} spaces recorded; {ins.shelters.capacity_unrecorded} shelters have no capacity on file{ins.shelters.capacity_implausible ? `, ${ins.shelters.capacity_implausible} implausible values flagged` : ""}. {ins.shelters.verified_on_maps} shelters have been matched on <GoogleMaps /> (place_id stored).</div>
      </div>

      <div className="card span-6">
        <h2>Reach without a car</h2>
        <div className="sub">Share of households without a car whose tract centroid is within {ins.reachable_km} km (straight line) of a shelter.</div>
        <PairBars rows={[
          { label: "…any shelter", a: reachAny, b: pct(s.households_no_vehicle_within_reach_any, s.households_no_vehicle) },
          { label: "…a recorded-accessible one", a: reachWc, b: pct(s.households_no_vehicle_within_reach_wheelchair_yes, s.households_no_vehicle), note: "Counts only shelters where somebody recorded yes: an upper bound on the good news." },
          { label: "Tracts with none in reach", a: pct(c.tracts_no_wheelchair_yes_within_reach, c.tracts), b: pct(s.tracts_no_wheelchair_yes_within_reach, s.tracts) },
        ]} />
        <div className="insight">{fmt(c.households_no_vehicle_within_reach_any)} of {fmt(c.households_no_vehicle)} no-car households can reach some shelter on foot, but only {fmt(c.households_no_vehicle_within_reach_wheelchair_yes)} can reach one recorded as wheelchair accessible. {pickupCount} of {tracts.length} tracts have no shelter of any kind within {reachKm} km: those need a pickup point.</div>
      </div>

      <div className="card span-7">
        <h2>Access gap by tract</h2>
        <div className="sub">Right of the line: no shelter recorded as wheelchair accessible within {reachKm} km. Higher: more households without a car.</div>
        <AccessScatter tracts={tracts} reachKm={reachKm} onSelect={onSelectTract} />
      </div>
      <div className="card span-5">
        <h2>Far from access, many without a car</h2>
        <div className="sub">Tracts beyond {reachKm} km of a recorded-accessible shelter, most no-car households first.</div>
        <table className="data">
          <thead><tr><th>Tract</th><th className="r">No-car hh</th><th className="r">Accessible km</th><th className="r">Any km</th></tr></thead>
          <tbody>{farMany.map((t) => (
            <tr key={t.geo_id} style={{ cursor: "pointer" }} onClick={() => onSelectTract(t.geo_id)}>
              <td>{tractLabel(t.geo_id)}</td><td className="r">{fmt(t.households_no_vehicle)}</td>
              <td className="r">{fmt(t.nearest_wheelchair_yes_km, 1)}</td><td className="r">{fmt(t.nearest_shelter_km, 1)}</td></tr>))}</tbody>
        </table>
        <div className="small muted" style={{ marginTop: 6 }}>Click a row to see it on the map with the shelters around it.</div>
      </div>

      <div className="card span-6">
        <h2>{ins.name} against Florida</h2>
        <div className="sub">Population-weighted. Mobility disability is CDC PLACES modeled prevalence, not a count.</div>
        <PairBars max={50} rows={[
          { label: "Households without a car", a: bc.pct_no_vehicle, b: bs.pct_no_vehicle, note: "car ownership, not lacking a ride" },
          { label: "Aged 65+", a: bc.pct_age_65_plus, b: bs.pct_age_65_plus },
          { label: "With a disability", a: bc.pct_disability, b: bs.pct_disability },
          { label: "Mobility disability (modeled)", a: bc.pct_mobility, b: bs.pct_mobility },
          { label: "Mobile homes per household", a: bc.pct_mobile_homes, b: bs.pct_mobile_homes },
          { label: "Below poverty line", a: bc.poverty_rate, b: bs.poverty_rate, note: "audit only; not a ranking input" },
        ]} />
        <div className="small muted">Lack of reliable transport (CDC PLACES) is not published for Florida, so car ownership stands in for it, and it is not the same thing.</div>
      </div>

      <div className="card span-6">
        <h2>Where this county sits in Florida</h2>
        <div className="sub">Need × hurricane/coastal-flood exposure (mean of tract scores, normalised across counties). Top 15; click to switch county.</div>
        <RankBars rows={ranked.slice(0, 15).concat(selCounty && selCounty.rank > 15 ? [selCounty] : []).map((x) => ({
          id: x.county_fips, label: `#${x.rank} ${x.name.replace(" County", "")}`, value: ((x.need_score ?? 0) * (x.exposure_score ?? 0)) / maxRank,
          note: `need ${fmt(x.need_score, 2)} · exposure ${fmt(x.exposure_score, 2)} · ${x.shelters.total} shelters, ${x.shelters.wheelchair.unrecorded} with wheelchair access not recorded`,
        }))} onClick={onCounty} selected={county} max={1} />
        <div className="small muted">Shelter counts are shown in the tooltip but never used to rank: they measure paperwork, not readiness.</div>
      </div>

      <div className="card span-12">
        <h2>Tract explorer</h2>
        <div className="sub">All {tracts.length} tracts with people in {ins.name}. Click a column to sort; click a row to open it on the map.</div>
        <div className="filters">
          <button aria-pressed={pickupOnly} onClick={() => setPickupOnly(!pickupOnly)}>Pickup needed only ({pickupCount})</button>
          <input placeholder="Search tract id or number" value={q} onChange={(e) => setQ(e.target.value)}
            style={{ border: "1px solid var(--border)", borderRadius: 14, padding: "3px 10px", background: "var(--surface)" }} />
        </div>
        <div className="table-wrap">
          <table className="data">
            <thead><tr>
              <th>Tract</th><th>Reach</th>
              {COLS.map((col) => (
                <th key={col.key} className="r" onClick={() => setSort({ key: col.key, desc: sort.key === col.key ? !sort.desc : true })}
                  aria-sort={sort.key === col.key ? (sort.desc ? "descending" : "ascending") : "none"}>
                  {col.label}{sort.key === col.key ? (sort.desc ? " ▼" : " ▲") : ""}
                </th>))}
              <th className="r">Mobility % <span className="tag modeled">M</span></th>
            </tr></thead>
            <tbody>{rows.map((t) => {
              const cls = reachClass(t, reachKm);
              return (
                <tr key={t.geo_id} style={{ cursor: "pointer" }} onClick={() => onSelectTract(t.geo_id)}>
                  <td>{tractLabel(t.geo_id)} <span className="muted small">{t.geo_id}</span></td>
                  <td>{cls === "pickup" ? <b style={{ color: "var(--critical)" }}>✕ pickup</b> : cls === "walkable" ? <span style={{ color: "var(--good)" }}>✓ walkable</span> : "–"}</td>
                  {COLS.map((col) => <td key={col.key} className="r">{fmt(t[col.key] as number | null, col.digits)}</td>)}
                  <td className="r">{fmt(t.pct_mobility, 1)}</td>
                </tr>);
            })}</tbody>
          </table>
        </div>
      </div>

      {brief && (
        <div className="card span-12">
          <h2>From the latest brief</h2>
          <div className="sub">{brief.headline}</div>
          <div className="small">{brief.destinations.length} shelters checked on <GoogleMaps />; {brief.destinations.filter((d) => d.verdict === "do_not_rely").length} dropped; {brief.fixes.length} fixes; {brief.unknowns.length} named unknowns. Open <b>August brief</b> for the full brief and the equity audit.</div>
        </div>
      )}
    </div>
  );
}
