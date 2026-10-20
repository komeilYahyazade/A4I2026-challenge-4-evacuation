import { Fragment, useMemo, useState } from "react";
import type { Affected, AuditResponse, Brief, CountyInsights, CountyMap, Driver, Selection, Tract } from "../types";
import { FLAG_LABEL, VERDICT, VSTATUS, fmt, hhmm, km, pct, reachClass } from "../format";
import { FlagChip, GoogleMaps, Segments, Tag } from "./Attribution";
import { AuditBars, Tile, TriBar } from "./Charts";

const SOURCE_TAG: Record<string, "recorded" | "modeled"> = { acs: "recorded", cdc_svi: "recorded", cms: "recorded", fema_nss: "recorded", cdc_places: "modeled" };

function driverText(d: Driver, ins: CountyInsights | null): string {
  const s = ins?.baseline.state;
  const v = d.value;
  switch (d.metric) {
    case "households_no_vehicle": return `${fmt(v)} households without a car`;
    case "pct_no_vehicle": return `${fmt(v, 1)}% of households without a car${s?.pct_no_vehicle != null ? ` (state ${fmt(s.pct_no_vehicle, 1)}%)` : ""}`;
    case "pop_65_plus": return `${fmt(v)} people aged 65+`;
    case "pct_disability": return `${fmt(v, 1)}% with a disability${s?.pct_disability != null ? ` (state ${fmt(s.pct_disability, 1)}%)` : ""}`;
    case "mobile_homes": return `${fmt(v)} mobile homes`;
    case "group_quarters_pop": return `${fmt(v)} people in group quarters`;
    case "nearest_wheelchair_yes_km": return `nearest shelter recorded as wheelchair accessible: ${km(v)} straight line`;
    case "certified_beds": return `${fmt(v)} certified beds`;
    case "nearest_generator_yes_km": return `nearest shelter with a recorded generator: ${km(v)} straight line`;
    default: return `${d.metric.replace(/_/g, " ")}: ${fmt(v, 1)} ${d.unit}`;
  }
}

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

export function CountySnapshot({ ins, map, county }: { ins: CountyInsights | null; map: CountyMap | null; county: string }) {
  if (!ins) return <div className="empty">Loading {county}…</div>;
  const evac = (f: string) => ins.flags.find((r) => r.scope === "evac" && r.flag === f);
  const c = ins.access.county;
  return (
    <>
      <h2 style={{ fontSize: 17 }}>{ins.name} today <Tag kind="recorded" /></h2>
      <div className="muted small">Before any live check. Press <b>Generate brief</b> to rank who cannot leave and check shelters on <GoogleMaps />.</div>
      {(["wheelchair_accessible", "in_surge_slosh_area", "generator_onsite"] as const).map((f) => {
        const r = evac(f);
        return r && <TriBar key={f} label={`${FLAG_LABEL[f]}, pre-landfall (EVAC) shelters`} yes={r.yes} no={r.no} unrecorded={r.unrecorded}
          note={f === "in_surge_slosh_area" ? "blank does not mean outside the zone" : undefined} />;
      })}
      <div className="tiles">
        <Tile value={fmt(c.households_no_vehicle)} label="households without a car" ctx={`${fmt(pct(c.households_no_vehicle_within_reach_wheelchair_yes, c.households_no_vehicle), 0)}% within ${ins.reachable_km} km of a recorded-accessible shelter`} />
        <Tile value={fmt(ins.power.power_dependent_dme)} label="power-dependent Medicare beneficiaries" tag={<Tag kind="county" />} ctx={ins.power.is_censored ? "1 to 10 (censored)" : "how many, not where"} />
        <Tile value={km(c.median_km_wheelchair_yes)} label="median distance to a recorded-accessible shelter" ctx={`vs ${km(c.median_km_any)} to any shelter`} />
      </div>
      {map && <div className="insight">{fmt(map.shelters.length)} shelters on file, {fmt(ins.shelters.evac)} for use before landfall. {fmt(ins.care.far_from_generator)} of {fmt(ins.care.facilities)} nursing homes have no shelter with a recorded generator within {ins.care.threshold_km} km.</div>}
    </>
  );
}

export function BriefPanel({ brief, ins, map, audit, reachKm, onSelect, onHighlight, selection, onFixList }: {
  brief: Brief; ins: CountyInsights | null; map: CountyMap | null; audit: AuditResponse | null; reachKm: number;
  onSelect: (s: Selection) => void; onHighlight: (a: Affected) => void; selection: Selection; onFixList: () => void;
}) {
  const [whoTab, setWhoTab] = useState<"tract" | "care_facility">("tract");
  const tracts = useMemo(() => new Map((map?.tracts ?? []).map((t) => [t.geo_id, t])), [map]);
  const dests = brief.destinations;
  const checked = dests.filter((d) => d.verification);
  const byStatus = (s: string) => checked.filter((d) => d.verification?.status === s);
  const blanksAnswered = checked.filter((d) => d.verification?.wheelchair.agreement === "record_blank").length;
  const blanks = checked.filter((d) => d.shelter.wheelchair_accessible === "unrecorded").length;
  const dropped = dests.filter((d) => d.verdict === "do_not_rely");
  const evac = (f: string) => ins?.flags.find((r) => r.scope === "evac" && r.flag === f);
  const who = brief.who_cannot_leave.filter((w) => w.kind === whoTab);
  const isSel = (kind: string, id: string) => selection?.kind === kind && selection.id === id;

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 8 }}>
        <h2 style={{ fontSize: 17 }}>{brief.county.name} readiness brief</h2>
        <span className="badge live">LIVE</span>
      </div>
      <div className="muted small">Generated {hhmm(brief.generated_at)} · {checked.length} shelters checked on <GoogleMaps /> · {brief.model}</div>
      <div className="card" style={{ marginTop: 10, borderLeft: "4px solid var(--accent)" }}><b>{brief.headline}</b></div>

      {["wheelchair_accessible", "in_surge_slosh_area"].map((f) => {
        const r = evac(f);
        return r && <TriBar key={f} label={`${FLAG_LABEL[f]}, pre-landfall shelters`} yes={r.yes} no={r.no} unrecorded={r.unrecorded}
          note={f === "in_surge_slosh_area" ? "blank does not mean outside the zone" : undefined} />;
      })}
      <div className="tiles">
        <Tile value={fmt(brief.power_dependence?.power_dependent_dme)} label="power-dependent residents" tag={<Tag kind="county" />}
          ctx={brief.power_dependence?.is_censored ? "1 to 10 (censored)" : "how many, not where"} />
        <Tile value={fmt(ins?.access.county.households_no_vehicle)} label="households without a car" ctx="ACS: car ownership, not rides" />
        <Tile value={fmt(ins?.shelters.capacity_recorded)} label="recorded shelter spaces" ctx={ins ? `${ins.shelters.capacity_unrecorded} shelters with capacity not recorded` : undefined} />
      </div>

      <div className={`card${dropped.length ? " alert" : ""}`}>
        <b>What <GoogleMaps /> changed</b> <span className="tag live">LIVE</span>
        <div className="tiles" style={{ gridTemplateColumns: "repeat(4, 1fr)" }}>
          <Tile value={byStatus("confirmed").length} label="confirmed" />
          <Tile value={byStatus("renamed").length} label="renamed" />
          <Tile value={byStatus("closed").length + byStatus("not_found").length} label="closed or missing" />
          <Tile value={`${blanksAnswered}/${blanks}`} label="blank wheelchair records Maps answered" />
        </div>
        {dropped.map((d) => (
          <div key={d.shelter.shelter_id} className="small">✕ <b>{d.shelter.name}</b> dropped: {d.verification ? VSTATUS[d.verification.status].label.toLowerCase() : "do not rely"}
            {d.serves.length > 0 && <> · was the nearest option for {d.serves.join(", ")}</>}</div>
        ))}
        {blanksAnswered > 0 && <div className="small muted" style={{ marginTop: 4 }}>A Maps "accessible entrance" fills a blank record but does not close it: those stay <i>verify before season</i>.</div>}
      </div>

      <div className="section-title"><span className="n">1</span>Who cannot leave</div>
      <div className="filters">
        <button aria-pressed={whoTab === "tract"} onClick={() => setWhoTab("tract")}>Can't drive: tracts ({brief.who_cannot_leave.filter((w) => w.kind === "tract").length})</button>
        <button aria-pressed={whoTab === "care_facility"} onClick={() => setWhoTab("care_facility")}>Nursing homes ({brief.who_cannot_leave.filter((w) => w.kind === "care_facility").length})</button>
      </div>
      <div className="muted small" style={{ marginBottom: 8 }}>Ranked by need score from physical and demographic inputs only: no accessibility field, no race.</div>
      {who.map((w) => {
        const t: Tract | undefined = tracts.get(w.id);
        const cls = t ? reachClass(t, reachKm) : "unknown";
        return (
          <div key={w.id} className={`card clickable${isSel(w.kind === "tract" ? "tract" : "care", w.id) ? " selected" : ""}`}
            onClick={() => onSelect({ kind: w.kind === "tract" ? "tract" : "care", id: w.id })}>
            <b>#{w.rank} {w.label}</b> <span className="muted small">{w.id} · {fmt(w.people)} {w.kind === "tract" ? "people" : "beds"}</span>
            <ul style={{ margin: "6px 0", paddingLeft: 18 }}>
              {w.drivers.map((d) => <li key={d.metric}>{driverText(d, ins)} <Tag kind={SOURCE_TAG[d.source] ?? "recorded"} /></li>)}
              {t?.pct_mobility != null && <li>Mobility disability {fmt(t.pct_mobility, 0)}% <Tag kind="modeled" /></li>}
            </ul>
            <div>{w.reason}</div>
            {t && cls !== "unknown" && (
              <div className="class-line">
                {cls === "walkable"
                  ? <><b className="walk">WALKABLE</b> · nearest shelter {km(t.nearest_shelter_km)} straight line, within {reachKm} km on foot. Plan a ride for residents who cannot walk that far.</>
                  : <><b className="pickup">PICKUP NEEDED</b> · nearest shelter {km(t.nearest_shelter_km)} straight line. {fmt(t.households_no_vehicle)} households have no car and no shelter within walking distance: stage a pickup point.</>}
              </div>
            )}
          </div>
        );
      })}
      <details className="card">
        <summary>Why this ranking, and how we measured</summary>
        <dl className="kv" style={{ marginTop: 8 }}>
          {Object.entries(brief.assumptions).map(([k, v]) => <Fragment key={k}><dt>{k.replace(/_/g, " ")}</dt><dd>{String(v)}</dd></Fragment>)}
        </dl>
        <div className="small muted" style={{ marginTop: 6 }}>Race is never an input; it appears only in the equity audit below.</div>
      </details>

      <div className="section-title"><span className="n">2</span>Where they would go, and whether it holds up</div>
      <div className="muted small" style={{ marginBottom: 8 }}>FEMA record and live <GoogleMaps /> check side by side. Sources appear directly under the claim they support.</div>
      {dests.map((d) => {
        const v = d.verification, s = d.shelter, vd = VERDICT[d.verdict];
        return (
          <div key={s.shelter_id} className={`card clickable${isSel("shelter", s.shelter_id) ? " selected" : ""}`} onClick={() => onSelect({ kind: "shelter", id: s.shelter_id })}>
            <span className={`verdict ${vd.cls}`}>{vd.icon} {vd.label}</span> <b>{s.name}</b>
            <div className="muted small">{s.address} · {s.usage ?? "usage n/a"} · capacity {s.evacuation_capacity ?? "not recorded"}{s.is_medical ? " · medical-needs" : ""}</div>
            <div className="small">{km(d.straight_line_km)} straight line
              {v?.drive?.drive_minutes != null && <> · <b>{fmt(v.drive.drive_minutes, 0)} min drive</b> (<GoogleMaps /> routing)</>}
              {d.serves.length > 0 && <> · serves {d.serves.join(", ")}</>}</div>
            <div style={{ margin: "4px 0" }}>
              <FlagChip label="Wheelchair" value={s.wheelchair_accessible} /><FlagChip label="Generator" value={s.generator_onsite} />
              <FlagChip label="Surge zone" value={s.in_surge_slosh_area} /><FlagChip label="Floodplain" value={s.in_100_yr_floodplain} />
            </div>
            {v && (
              <div style={{ borderTop: "1px solid var(--border)", marginTop: 6, paddingTop: 6 }}>
                <span className={`verdict ${VSTATUS[v.status].cls}`}>{VSTATUS[v.status].icon} {VSTATUS[v.status].label}</span>{" "}
                <span className="small">{v.maps_name && v.maps_name !== s.name ? <>now <b>{v.maps_name}</b></> : null} {v.place_type && <span className="muted">· {v.place_type}</span>}</span>
                <Segments segments={v.summary} sources={v.sources} checkedAt={v.checked_at} />
              </div>
            )}
            <div className="small" style={{ fontWeight: 600, marginTop: 4 }}>Why this verdict</div>
            <Segments segments={d.verdict_reason} sources={brief.sources} checkedAt={v?.checked_at} />
          </div>
        );
      })}

      <div className="section-title"><span className="n">3</span>What to fix before the season</div>
      {brief.fixes.slice(0, 5).map((f) => (
        <div key={f.priority} className="card">
          <b>{f.priority}. {f.title}</b>
          <div className="small">{f.recommendation}</div>
          <button className="btn small" style={{ marginTop: 6 }} onClick={() => onHighlight(f.affected)}>Show {f.affected.tracts.length + f.affected.shelters.length + f.affected.care_facilities.length} on map</button>
        </div>
      ))}
      <button className="btn" onClick={onFixList}>Open the full fix list ({brief.fixes.length}) →</button>

      <div className="section-title"><span className="n">4</span>What we could not tell you</div>
      {brief.unknowns.map((u) => (
        <div key={u.topic} className="card">
          <b>{u.topic}</b>: <span className="num">{u.count == null ? "not known" : <b>{fmt(u.count)}{u.of != null ? ` of ${fmt(u.of)}` : ""} {u.unit}</b>}</span>
          <div className="small">{u.why}</div>
          <div className="small muted">How to close it: {u.how_to_close}</div>
        </div>
      ))}

      <div className="section-title">Equity audit</div>
      {audit ? (<>
        <div className="muted small">{audit.compares}. Did the plan land where people cannot leave, or where somebody filled in the form?</div>
        <AuditBars rows={audit.rows} />
        {audit.rows.filter((r) => r.unit === "USD").map((r) => (
          <div key={r.metric} className="small">Median income: brief ${fmt(r.recommended)} · county ${fmt(r.county)} · state ${fmt(r.state)}</div>
        ))}
        {audit.finding && <div className="insight">{audit.finding}</div>}
      </>) : <div className="muted small">Loading audit…</div>}

      <div style={{ margin: "16px 0" }}>
        <a className="btn" download={`readiness_brief_${brief.county.county_fips}.json`}
          href={`data:application/json;charset=utf-8,${encodeURIComponent(exportBrief(brief))}`}>Download brief (FEMA data + place_id only)</a>
      </div>
    </div>
  );
}
